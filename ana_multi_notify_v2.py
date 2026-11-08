
import asyncio
from datetime import date, timedelta

from playwright.async_api import async_playwright

from ana_save_calendar import (
    PROFILE_DIR,
    extract_calendar,
    find_calendar_page,
)

from ana_multi_notify import (
    convert_dates,
    show_dates,
    find_matches,
    save_candidates,
    create_message,
)

from ana_line_notify import send_line_message


# ==========================================
# 基本設定
# ==========================================

MIN_DAYS = 4
MAX_DAYS = 5

# LINE通知の確認なしで送信
SEND_LINE_AUTOMATICALLY = True

# 往路選択を確認する最大回数
MAX_SELECTION_ATTEMPTS = 3


# ==========================================
# ユーザー入力
# ==========================================

async def ask(message):

    return await asyncio.to_thread(
        input,
        message
    )


# ==========================================
# ANA画面の選択済み往路日付を取得
# ==========================================

async def get_selected_outbound(page):

    result = await page.evaluate(
        r"""() => {

            let currentRoute = null;

            const selected = [];

            const elements = document.querySelectorAll(
                "body *"
            );

            for (const element of elements) {

                const text = (
                    element.textContent || ""
                ).trim();

                // 往路と復路の見出しを区別する

                if (
                    text === "往路" ||
                    text === "復路"
                ) {

                    const childHasSameText =
                        Array.from(
                            element.children
                        ).some(
                            child =>
                                (
                                    child.textContent || ""
                                ).trim() === text
                        );

                    if (!childHasSameText) {

                        currentRoute = (
                            text === "往路"
                            ? "outbound"
                            : "return"
                        );
                    }
                }

                // 往路以外は対象にしない

                if (currentRoute !== "outbound") {
                    continue;
                }

                // 日付のセルだけを対象にする

                if (!element.matches(
                    "td.flightSelectParent"
                )) {
                    continue;
                }

                if (
                    element.getClientRects().length === 0
                ) {
                    continue;
                }

                // 選択済みのラジオボタンを探す

                const radio = element.querySelector(
                    'input[type="radio"]:checked'
                );

                if (!radio) {
                    continue;
                }

                // 日付の見出しを取得

                const label = element.querySelector(
                    ".simpleCalendarDateGroup label"
                );

                if (!label) {
                    continue;
                }

                const match = (
                    label.textContent || ""
                ).match(
                    /(\d{1,2})月(\d{1,2})日/
                );

                if (!match) {
                    continue;
                }

                selected.push({
                    month: Number(match[1]),
                    day: Number(match[2])
                });
            }

            return selected;
        }"""
    )

    return result


# ==========================================
# 選択された日付を年付きで判定
# ==========================================

def resolve_selected_date(
    records,
    reference_date
):

    if len(records) != 1:

        raise ValueError(
            "選択済みの往路日付を"
            "1件に特定できません。"
        )

    dates = convert_dates(
        records,
        reference_date
    )

    if len(dates) != 1:

        raise ValueError(
            "選択済み往路日付を"
            "変換できませんでした。"
        )

    return dates[0]


# ==========================================
# 往路の選択状態を確認
# ==========================================

async def verify_outbound_selection(
    page,
    expected_date
):

    print()
    print(
        f"確認対象の往路：{expected_date}"
    )

    for attempt in range(
        MAX_SELECTION_ATTEMPTS
    ):

        print()
        print(
            f"選択確認 "
            f"{attempt + 1}/"
            f"{MAX_SELECTION_ATTEMPTS}"
        )

        print(
            "ANAのブラウザで"
            "対象の日付を選択してください。"
        )

        answer = await ask(
            "選択後Enter／sでスキップ／"
            "qで終了："
        )

        answer = answer.strip().lower()

        if answer == "q":
            return "quit"

        if answer == "s":
            return "skip"

        # 復路表示の更新を少し待つ
        await page.wait_for_timeout(800)

        records = await get_selected_outbound(
            page
        )

        try:

            actual_date = resolve_selected_date(
                records,
                expected_date
            )

        except ValueError as error:

            print()
            print(
                f"選択状態を確認できません：{error}"
            )

            continue

        print(
            f"画面で選択されている往路："
            f"{actual_date}"
        )

        if actual_date == expected_date:

            print(
                "成功：指定された往路日付と"
                "一致しています。"
            )

            return "verified"

        print()
        print(
            "注意：指定された日付と"
            "一致していません。"
        )

        print(
            f"期待値：{expected_date}"
        )

        print(
            f"実際値：{actual_date}"
        )

    return "failed"


# ==========================================
# 復路の空席日付を取得
# ==========================================

async def get_return_dates(
    page,
    outbound
):

    result = await extract_calendar(
        page
    )

    reference_date = (
        outbound + timedelta(days=MIN_DAYS)
    )

    return_dates = convert_dates(
        result["return"],
        reference_date
    )

    show_dates(
        f"往路 {outbound} に対する復路",
        return_dates
    )

    return return_dates


# ==========================================
# 重複候補の除去
# ==========================================

def remove_duplicates(candidates):

    unique = {}

    for item in candidates:

        key = (
            item["往路出発日"],
            item["復路出発日"]
        )

        unique[key] = item

    return sorted(
        unique.values(),
        key=lambda item: (
            item["往路出発日"],
            item["復路出発日"]
        )
    )


# ==========================================
# LINE通知
# ==========================================

async def notify_line(candidates):

    if not candidates:

        print()
        print(
            "条件に一致する候補が"
            "ありませんでした。"
        )

        print(
            "LINE通知は行いません。"
        )

        return

    message = create_message(
        candidates
    )

    print()
    print(
        "===== LINE通知内容 ====="
    )

    print(message)

    if not SEND_LINE_AUTOMATICALLY:

        answer = await ask(
            "LINEへ送信しますか？ (y/n)："
        )

        if answer.strip().lower() != "y":

            print(
                "LINE送信を中止しました。"
            )

            return

    success = send_line_message(
        message
    )

    if success:

        print()
        print(
            "LINE通知が完了しました。"
        )

    else:

        print()
        print(
            "LINE通知に失敗しました。"
        )


# ==========================================
# メイン処理
# ==========================================

async def main():

    print()
    print(
        "================================"
    )
    print(
        " ANA特典航空券・複数候補検索 V2"
    )
    print(
        "================================"
    )

    print()
    print(
        "ANAへのログイン・検索・"
        "往路選択は手動です。"
    )

    print(
        "選択した往路の日付を"
        "Pythonが自動確認します。"
    )

    # ======================================
    # STEP 1：検索基準日
    # ======================================

    reference_text = await ask(
        "\n検索基準の往路日付"
        "（例：2027-01-19）："
    )

    try:

        reference_date = date.fromisoformat(
            reference_text.strip()
        )

    except ValueError:

        print(
            "日付の形式が正しくありません。"
        )

        return

    # ======================================
    # STEP 2：ブラウザ起動
    # ======================================

    async with async_playwright() as p:

        context = await (
            p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=False,
                viewport={
                    "width": 1280,
                    "height": 900
                }
            )
        )

        try:

            if not context.pages:
                await context.new_page()

            print()
            print("ブラウザを起動しました。")

            print(
                "ANAへ手動でログインして、"
                "空席カレンダーを"
                "表示してください。"
            )

            await ask(
                "空席カレンダー表示後にEnter："
            )

            page = await find_calendar_page(
                context
            )

            if page is None:

                print(
                    "空席カレンダーが"
                    "見つかりません。"
                )

                return

            # ==================================
            # STEP 3：往路の空席日取得
            # ==================================

            result = await extract_calendar(
                page
            )

            outbound_dates = convert_dates(
                result["outbound"],
                reference_date
            )

            show_dates(
                "往路の空席候補",
                outbound_dates
            )

            if not outbound_dates:

                print(
                    "往路の空席候補が"
                    "ありません。"
                )

                return

            # ==================================
            # STEP 4：各往路を順番に確認
            # ==================================

            all_candidates = []

            completed = 0
            skipped = 0

            for index, outbound in enumerate(
                outbound_dates,
                start=1
            ):

                print()
                print(
                    "=============================="
                )

                print(
                    f"往路候補 "
                    f"{index}/{len(outbound_dates)}"
                )

                print(
                    f"対象日：{outbound}"
                )

                print(
                    "=============================="
                )

                status = (
                    await verify_outbound_selection(
                        page,
                        outbound
                    )
                )

                if status == "quit":

                    print(
                        "残りの候補の調査を"
                        "終了します。"
                    )

                    break

                if status == "skip":

                    skipped += 1

                    print(
                        "この往路をスキップしました。"
                    )

                    continue

                if status != "verified":

                    skipped += 1

                    print()
                    print(
                        "往路の日付を確認できないため、"
                        "この候補をスキップします。"
                    )

                    continue

                # ==================================
                # STEP 5：復路の空席日取得
                # ==================================

                return_dates = (
                    await get_return_dates(
                        page,
                        outbound
                    )
                )

                completed += 1

                # ==================================
                # STEP 6：4～5日差の判定
                # ==================================

                matches = find_matches(
                    outbound,
                    return_dates
                )

                if matches:

                    print()
                    print(
                        f"条件一致：{len(matches)}件"
                    )

                    for item in matches:

                        print(
                            item["往路出発日"],
                            "→",
                            item["復路出発日"]
                        )

                    all_candidates.extend(
                        matches
                    )

                else:

                    print(
                        "4～5日差の候補は"
                        "ありません。"
                    )

            # ==================================
            # STEP 7：重複除去
            # ==================================

            candidates = remove_duplicates(
                all_candidates
            )

            print()
            print(
                "===== 最終集計 ====="
            )

            print(
                f"確認した往路：{completed}件"
            )

            print(
                f"スキップ：{skipped}件"
            )

            print(
                f"条件一致：{len(candidates)}件"
            )

            # ==================================
            # STEP 8：CSV保存
            # ==================================

            save_candidates(
                candidates
            )

            # ==================================
            # STEP 9：LINE通知
            # ==================================

            await notify_line(
                candidates
            )

            print()
            print(
                "===== 処理終了 ====="
            )

            print(
                "※カレンダー上の空席候補です。"
            )

            print(
                "※現地宿泊数・便詳細・"
                "予約可能性は未確認です。"
            )

        except Exception as error:

            print()
            print(
                "エラーが発生しました。"
            )

            print(str(error))

        finally:

            await context.close()


if __name__ == "__main__":
    asyncio.run(main())
