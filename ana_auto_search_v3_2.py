"""ANA特典航空券：7日間隔で最大6回の検索を集約しCSV/LINEに通知するV3.2。

既存の ana_award_search.py、ana_auto_search_v2.py、ana_multi_notify.py、
ana_multi_notify_v2.py、ana_save_calendar.py、ana_line_notify.py を変更せず利用。

注意：ANAの画面構造は変化します。結果画面から検索フォームへ戻れない場合や
エラー・アクセス制限を検出した場合は、自動再試行せず停止します。
カレンダー上の空席候補は2名分の実際の予約可否を保証しません。
"""
import asyncio
import os
import re
from datetime import date, timedelta

from playwright.async_api import async_playwright, Error as PlaywrightError

from ana_award_search import (
    ANA_URL,
    PROFILE_DIR,
    MAX_SEARCHES,
    SEARCH_DELAY,
    SEARCH_INTERVAL_DAYS as OLD_SEARCH_INTERVAL_DAYS,
    add_months,
    click_visible,
    open_booking_page,
    login_ana,
    wait_for_search_form,
    fill_search_form,
    close_no_match_dialog,
    is_no_match_error,
)
import ana_award_search as award_search

from ana_auto_search_v2 import (
    ask,
    has_congestion,
    search_once_limited,
    inspect_calendar,
)
from ana_multi_notify_v2 import remove_duplicates
from ana_multi_notify import save_candidates, create_message
from ana_line_notify import send_line_message

# 確実に識別できた「検索条件へ戻る」操作だけを対象にする。
RETURN_LABELS = (
    "検索条件を変更",
    "検索条件の変更",
    "条件を変更",
    "検索条件を修正",
    "検索をやり直す",
)
RETURN_TIMEOUT_MS = 12000
MAX_BACK_ATTEMPTS = 1
SEARCH_INTERVAL_DAYS = 7
MAX_CALENDAR_MOVES = 5

# 日付カレンダーの表示月をDOMから読み取り、前後の方向を決める。
# ANAのHTML構造が想定と違えば停止し、誤った日付を選択しない。
async def calendar_months(page):
    """可視の #calsec0..2 に含まれる年月を取得する。"""
    result = []
    for index in range(3):
        section = page.locator(f"#calsec{index}")
        if await section.count() != 1 or not await section.is_visible():
            continue
        # 表示されているカレンダーの年月を安全に判定するため、
        # セクションの文字列と直近の属性値を参照する。
        evidence = await section.evaluate(r"""el => {
            const attrs = node => node && node.getAttributeNames
              ? node.getAttributeNames().map(k => `${k}=${node.getAttribute(k)}`).join(' ') : '';
            return [el.innerText || '', attrs(el),
                    attrs(el.querySelector('[aria-label]'))].join('\n');
        }""")
        # 2027年2月, 2027 / 02 などの表記に対応。
        found = re.findall(
            r"(20\d{2})\s*(?:年|[./-])\s*(0?[1-9]|1[0-2])\s*月?",
            evidence,
        )
        # 旧プログラムで利用した『2027年2月』の表記を優先。
        if not found:
            found = re.findall(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", evidence)
        unique = {(int(y), int(m)) for y, m in found if 1 <= int(m) <= 12}
        if len(unique) == 1:
            y, m = unique.pop()
            result.append((index, y, m, section))
        elif len(unique) > 1:
            raise RuntimeError(
                f"日付カレンダー{index}から複数の年月を検出したため停止します。"
            )
    return result


async def safe_navigate_calendar(page, target_date):
    """移動方向を判定できる場合に限り日付カレンダーを移動する。"""
    target = (target_date.year, target_date.month)
    visited = set()
    for attempt in range(MAX_CALENDAR_MOVES + 1):
        months = await calendar_months(page)
        shown = sorted({(year, month) for _, year, month, _ in months})
        print("日付カレンダー表示月：", ", ".join(
            f"{year}-{month:02d}" for year, month in shown
        ) or "取得不可")

        matching = [section for _, y, m, section in months if (y, m) == target]
        if len(matching) == 1:
            return matching[0]
        if len(matching) > 1:
            raise RuntimeError("対象月のカレンダーが複数あります。停止します。")
        if not shown:
            raise RuntimeError(
                "日付カレンダーの表示年月を判定できません。"
                "年月の見える画面を撮影して確認してください。"
            )

        if attempt >= MAX_CALENDAR_MOVES:
            break
        signature = tuple(shown)
        if signature in visited:
            raise RuntimeError("カレンダーの表示月が循環しています。停止します。")
        visited.add(signature)

        if target < shown[0]:
            label = "前の3ヶ月"
        elif target > shown[-1]:
            label = "次の3ヶ月"
        else:
            raise RuntimeError(
                "対象月が表示範囲内なのに見つかりません。"
                "画面を確認してください。"
            )

        buttons = page.get_by_text(label, exact=True)
        candidates = []
        for i in range(min(await buttons.count(), 8)):
            item = buttons.nth(i)
            if await item.is_visible() and await item.is_enabled():
                candidates.append(item)
        if len(candidates) != 1:
            raise RuntimeError(
                f"『{label}』の操作可能なボタンが{len(candidates)}件です。"
                "安全に月移動できないため停止します。"
            )
        print(f"対象月 {target_date:%Y-%m} に向けて『{label}』をクリック")
        await candidates[0].click(timeout=8000)
        await asyncio.sleep(0.8)
    raise RuntimeError(f"対象月 {target_date:%Y-%m} を表示できませんでした。")


# 元の set_trip_dates/select_date は内部で navigate_calendar を参照する。
# 既存ファイルを変更せずV3.2の安全な月移動に切り替える。
award_search.navigate_calendar = safe_navigate_calendar



async def is_form_ready(page):
    """検索フォームの出発地・到着地・日付欄が見えているか確認。"""
    if page is None or page.is_closed():
        return False
    try:
        names = ("出発地", "到着地", "往路出発日", "復路出発日")
        for name in names:
            loc = page.get_by_role("textbox", name=name, exact=True)
            if await loc.count() == 0 or not await loc.first.is_visible():
                return False
        return True
    except PlaywrightError:
        return False


async def wait_form(page, seconds=12):
    """検索フォーム復帰時、履歴から再表示された既知の古いエラーを閉じる。"""
    for _ in range(seconds * 2):
        if await has_congestion(page):
            raise RuntimeError("混雑・アクセス制限・メンテナンスを検出したため停止します。")

        # 今回の実画面で確認できたケース：履歴を戻ると以前の
        # E_A01P01_0006 ダイアログまで復元される。
        # 未知の警告やログイン・アクセス制限には触れない。
        if await is_no_match_error(page):
            print("以前の検索の E_A01P01_0006 が再表示されました。")
            await close_no_match_dialog(page)
            print("古いエラーを閉じて検索フォームを確認します。")

        if await is_form_ready(page):
            return True
        await asyncio.sleep(0.5)
    return False


async def click_return_button(page):
    """明示的な『条件変更』リンク/ボタンが1件だけあればクリック。"""
    if page is None or page.is_closed():
        return False
    for label in RETURN_LABELS:
        found = []
        for role in ("button", "link"):
            loc = page.get_by_role(role, name=label, exact=True)
            for i in range(min(await loc.count(), 8)):
                item = loc.nth(i)
                if await item.is_visible() and await item.is_enabled():
                    found.append(item)
        if len(found) == 1:
            print(f"検索条件に戻るボタンをクリック：{label}")
            await found[0].click(timeout=8000)
            return True
        if len(found) > 1:
            raise RuntimeError(f"『{label}』が複数見つかりました。誤操作防止のため停止します。")
    return False


async def return_to_search_form(context, booking_page, result_page):
    """検索結果から戻る。確実な復帰ができないときは打ち切る。"""
    print("\n次の日付を検索するため、検索条件画面へ戻ります（古いエラーの再表示にも対応）。")

    # 結果が別タブの場合、元のフォームが残っている可能性がある。
    if result_page is not booking_page and await wait_form(booking_page, seconds=3):
        if not result_page.is_closed():
            await result_page.close()
        print("元の検索タブに戻りました。")
        return booking_page

    # まずサイト側の明示的な「条件変更」操作を探す。
    for page in (result_page, booking_page):
        if page is None or page.is_closed():
            continue
        if await wait_form(page, seconds=3):
            print("検索フォームを確認しました。")
            return page
        if await click_return_button(page):
            if await wait_form(page):
                if result_page is not page and not result_page.is_closed():
                    await result_page.close()
                print("条件変更画面への復帰に成功しました。")
                return page
            # ボタンが新しいタブを開くケースは一度だけチェック。
            for new_page in reversed(context.pages):
                if not new_page.is_closed() and await wait_form(new_page, seconds=3):
                    print("新しいタブの検索フォームを確認しました。")
                    return new_page
            raise RuntimeError("条件変更ボタン後に検索フォームを確認できませんでした。")

    # 条件変更ボタンがないケースのみ、ブラウザ履歴を1回戻して確認する。
    page = result_page if result_page and not result_page.is_closed() else booking_page
    if page is None or page.is_closed():
        raise RuntimeError("検索結果のブラウザタブが閉じられています。")
    for _ in range(MAX_BACK_ATTEMPTS):
        print("条件変更ボタンがないため、ブラウザ履歴を1回戻します。")
        await page.go_back(wait_until="domcontentloaded", timeout=20000)
        if await wait_form(page):
            print("古いエラーがない検索フォームに戻りました。")
            return page
    raise RuntimeError(
        "検索フォームへ安全に戻れませんでした。追加検索を中止します。"
        " 画面の表示とPowerShellのログを確認してください。"
    )


async def check_search_form_state(page):
    """場所欄が空なら誤った条件での検索を防ぐ。人数は自動再加算しない。"""
    for label in ("出発地", "到着地"):
        field = page.get_by_role("textbox", name=label, exact=True).first
        value = (await field.input_value()).strip()
        if not value:
            raise RuntimeError(
                f"検索条件に戻った後、『{label}』が空です。"
                "人数などの条件が初期化された可能性があるため停止します。"
            )
    print("出発地・到着地の入力が残っていることを確認しました。")
    print("注意：人数・クラス等が保持されているかは、ANA画面でも確認してください。")


async def main():
    print("===== ANA特典航空券・複数日程検索 V3.2 =====")
    print("成田 ⇔ オークランド / 大人2名 / 特典エコノミー")
    print(f"最大検索回数：{MAX_SEARCHES}回")
    print(f"検索基準日の間隔：{SEARCH_INTERVAL_DAYS}日")
    print("空席カレンダーが表示されても、次の日付の検索を試みます。")
    print("アクセス制限・混雑・不明な画面では停止します。")
    print("空席表示は予約可否・現地宿泊数の保証ではありません。")

    password = os.getenv("ANA_PASSWORD")
    if not password:
        print("ANA_PASSWORD を読み込めません。既存の .env を確認してください。")
        return

    start_date = add_months(date.today(), 2)
    print(f"最初の検索基準日：{start_date}")
    all_candidates = []
    searched = 0
    completed_all = True
    stop_reason = None

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(ANA_URL, wait_until="domcontentloaded", timeout=60000)

            overseas = page.get_by_role("tab", name=re.compile("海外"))
            if await overseas.count() > 0:
                await click_visible(overseas, "海外タブ")
            award = page.get_by_role("tab", name="特典航空券", exact=True)
            if not await click_visible(award, "特典航空券タブ"):
                await ask("特典航空券タブを選択したらEnter：")

            booking = await open_booking_page(context, page)
            await login_ana(booking, password)
            await wait_for_search_form(booking)
            await fill_search_form(booking)

            for index in range(MAX_SEARCHES):
                target = start_date + timedelta(days=SEARCH_INTERVAL_DAYS * index)
                print(f"\n========== 検索 {index + 1}/{MAX_SEARCHES} ==========")
                print(f"基準の往路：{target}")
                if not await is_form_ready(booking):
                    completed_all = False
                    stop_reason = "検索フォームが表示されていません。"
                    break
                if index > 0:
                    await check_search_form_state(booking)
                if await has_congestion(booking):
                    completed_all = False
                    stop_reason = "混雑・制限を検出しました。"
                    break

                searched += 1
                status, result_page = await search_once_limited(context, booking, target)
                print(f"検索結果：{status}")

                if status == "no_match":
                    # 明示的に旅程なしと判定できた場合のみ、次の日付へ進む。
                    if result_page is not booking:
                        completed_all = False
                        stop_reason = "検索フォームとエラーの画面が異なります。"
                        break
                    await close_no_match_dialog(result_page)

                elif status == "calendar":
                    # 表示された空席候補を評価して集約する。
                    current = await inspect_calendar(context, result_page, target)
                    print(f"この検索画面で見つかった候補：{len(current)}件")
                    all_candidates.extend(current)

                else:
                    completed_all = False
                    stop_reason = (
                        "混雑・アクセス制限が検出されました。"
                        if status == "congestion" else
                        "検索結果を安全に判定できませんでした。"
                    )
                    break

                if index + 1 == MAX_SEARCHES:
                    continue

                # no_matchではダイアログが閉じてそのままフォームに戻ることがある。
                if status == "calendar":
                    try:
                        booking = await return_to_search_form(
                            context, booking, result_page
                        )
                    except (PlaywrightError, RuntimeError) as error:
                        completed_all = False
                        stop_reason = str(error)
                        break
                elif not await is_form_ready(booking):
                    completed_all = False
                    stop_reason = "空席なしの画面から検索フォームに戻れませんでした。"
                    break

                print(f"次の検索まで{SEARCH_DELAY}秒待機します。")
                await asyncio.sleep(SEARCH_DELAY)

            candidates = remove_duplicates(all_candidates)
            print("\n========== 最終結果 ==========")
            print(f"実施した検索：{searched}/{MAX_SEARCHES}回")
            print(f"重複除去後の候補：{len(candidates)}件")
            if not completed_all:
                print("一部未確認の検索があります。")
                print(f"停止理由：{stop_reason}")
            if candidates:
                save_candidates(candidates)
                print("CSVに保存しました。")
                message = create_message(candidates)
                if not completed_all:
                    notice = "【注意】予定した検索の一部は未完了です。\n\n"
                    if len(notice + message) > 4900:
                        print("LINEメッセージが長すぎるため通知しません。CSVを確認してください。")
                        message = None
                    else:
                        message = notice + message
                if message is not None:
                    print("\n===== LINE通知内容 =====\n" + message)
                    answer = (await ask("LINEへ送信しますか？ (y/n)：")).strip().lower()
                    if answer == "y":
                        if send_line_message(message):
                            print("LINEへの通知が完了しました。")
                        else:
                            print("LINE送信に失敗しました。CSVは保存済みです。")
                    else:
                        print("LINE通知はキャンセルしました。CSVは保存済みです。")
            else:
                print("今回確認した範囲では条件一致候補なし。")
                print("既存のCSVは上書きせず、LINE通知も送りません。")
            await ask("ブラウザを確認したらEnterで終了：")

        except (PlaywrightError, RuntimeError, ValueError) as error:
            print(f"\n処理を停止しました：{error}")
            if all_candidates:
                print(f"停止前に確認できた候補：{len(remove_duplicates(all_candidates))}件")
                print("エラー終了のため自動通知は行いません。")
            await ask("画面を確認したらEnterで終了：")
        finally:
            await context.close()


if __name__ == "__main__":
    asyncio.run(main())
