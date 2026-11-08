
import asyncio
import csv
import re

from datetime import date
from pathlib import Path

from playwright.async_api import async_playwright


# ==========================================
# 基本設定
# ==========================================

BASE_DIR = Path(__file__).resolve().parent

PROFILE_DIR = (
    Path.home() / "ana_automation_profile"
)

OUTPUT_FILE = (
    BASE_DIR / "ana_roundtrip_candidates.csv"
)

# 今回調査する検索結果の年
SEARCH_YEAR = 2027

# 希望する往路・復路出発日の差
MIN_DAYS = 4
MAX_DAYS = 5


# ==========================================
# 空席カレンダーの日付を取得
# ==========================================

async def extract_calendar(page):

    results = await page.evaluate(
        r"""() => {

            const result = {
                outbound: [],
                return: [],
                unknown: []
            };

            let currentRoute = null;

            // 画面上の要素を表示順に調べる
            const elements = document.querySelectorAll(
                "body *"
            );

            for (const element of elements) {

                const text = (
                    element.textContent || ""
                ).trim();

                // 往路・復路の見出しを検出
                // 子要素が同じ見出しを持つ場合、
                // 親要素は見出しとして扱わない。

                if (text === "往路" || text === "復路") {

                    const childHasSameText =
                        Array.from(element.children).some(
                            child =>
                                (child.textContent || "").trim()
                                === text
                        );

                    if (!childHasSameText) {
                        currentRoute = (
                            text === "往路"
                            ? "outbound"
                            : "return"
                        );
                    }
                }

                // 空席日付のセル
                if (
                    !element.matches(
                        "td.flightSelectParent"
                    )
                ) {
                    continue;
                }

                if (
                    element.getClientRects().length === 0
                ) {
                    continue;
                }

                // 空席ありの表示を確認
                if (
                    !element.textContent.includes(
                        "空席あり"
                    )
                ) {
                    continue;
                }

                // 日付選択ラジオボタンが
                // 存在することを確認
                const radio = element.querySelector(
                    'input[type="radio"]'
                );

                if (!radio || radio.disabled) {
                    continue;
                }

                // 日付を取得
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

                const record = {
                    month: Number(match[1]),
                    day: Number(match[2])
                };

                if (currentRoute === "outbound") {
                    result.outbound.push(record);
                } else if (currentRoute === "return") {
                    result.return.push(record);
                } else {
                    result.unknown.push(record);
                }
            }

            return result;
        }"""
    )

    return results


# ==========================================
# 日付をPython形式へ変換
# ==========================================

def convert_dates(records):

    converted = set()

    for record in records:

        month = record["month"]
        day = record["day"]

        try:

            item = date(
                SEARCH_YEAR,
                month,
                day
            )

            converted.add(item)

        except ValueError:

            print(
                f"無効な日付：{month}月{day}日"
            )

    return sorted(converted)


# ==========================================
# 空席日付を表示
# ==========================================

def show_dates(title, dates):

    print()
    print(f"===== {title} =====")

    if not dates:
        print("取得できませんでした。")
        return

    for item in dates:
        print(item.isoformat())

    print(f"取得件数：{len(dates)}件")


# ==========================================
# 4～5日差の組み合わせを抽出
# ==========================================

def find_roundtrip_candidates(
    outbound_dates,
    return_dates
):

    candidates = []

    for outbound in outbound_dates:

        for inbound in return_dates:

            days = (
                inbound - outbound
            ).days

            if MIN_DAYS <= days <= MAX_DAYS:

                candidates.append({
                    "往路出発日": outbound.isoformat(),
                    "復路出発日": inbound.isoformat(),
                    "日付差": days,
                    "確認状況": (
                        "カレンダー空席表示のみ・"
                        "便詳細未確認"
                    )
                })

    return candidates


# ==========================================
# CSVへ保存
# ==========================================

def save_candidates(candidates):

    columns = [
        "往路出発日",
        "復路出発日",
        "日付差",
        "確認状況"
    ]

    with OUTPUT_FILE.open(
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=columns
        )

        writer.writeheader()

        writer.writerows(candidates)

    print()
    print("CSV保存が完了しました。")
    print(f"保存先：{OUTPUT_FILE}")


# ==========================================
# 入力待ち
# ==========================================

async def wait_for_enter(message):

    await asyncio.to_thread(
        input,
        message
    )


# ==========================================
# カレンダーが表示されたタブを探す
# ==========================================

async def find_calendar_page(context):

    for page in reversed(context.pages):

        if page.is_closed():
            continue

        cells = page.locator(
            "td.flightSelectParent"
        )

        if await cells.count() > 0:
            return page

    return None


# ==========================================
# メイン処理
# ==========================================

async def main():

    print()
    print("===== ANA往復空席候補抽出 =====")
    print()
    print("ANAへの自動検索は行いません。")
    print("ブラウザ操作は手動です。")

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

            print()
            print("ブラウザを開きました。")
            print()
            print(
                "ANAサイトを手動で開き、"
                "特典航空券の空席カレンダーを"
                "表示してください。"
            )

            await wait_for_enter(
                "往路の空席カレンダー表示後にEnter："
            )

            page = await find_calendar_page(
                context
            )

            if page is None:

                print(
                    "空席カレンダーが見つかりません。"
                )
                return

            # ==================================
            # STEP 1：往路の空席日を取得
            # ==================================

            first_result = await extract_calendar(
                page
            )

            outbound_dates = convert_dates(
                first_result["outbound"]
            )

            show_dates(
                "往路の空席日",
                outbound_dates
            )

            if not outbound_dates:

                print(
                    "往路の日付を取得できませんでした。"
                )
                return

            # ==================================
            # STEP 2：往路を手動選択
            # ==================================

            print()
            print(
                "ANAのブラウザに戻ってください。"
            )
            print(
                "往路の空席日を1つ選択してください。"
            )
            print(
                "復路の空席日が表示されたら、"
                "PowerShellでEnterを押してください。"
            )

            await wait_for_enter(
                "復路カレンダー表示後にEnter："
            )

            # ==================================
            # STEP 3：復路の空席日を取得
            # ==================================

            second_result = await extract_calendar(
                page
            )

            return_dates = convert_dates(
                second_result["return"]
            )

            show_dates(
                "復路の空席日",
                return_dates
            )

            if not return_dates:

                print()
                print(
                    "復路の空席日を取得できませんでした。"
                )
                print(
                    "画面の構造を再確認する必要があります。"
                )
                return

            # ==================================
            # STEP 4：選択した往路の日付を確認
            # ==================================

            print()
            print(
                "今回手動で選択した往路日付を"
                "入力してください。"
            )

            selected_text = await asyncio.to_thread(
                input,
                "往路日付（例：2027-01-19）："
            )

            try:

                selected_outbound = date.fromisoformat(
                    selected_text.strip()
                )

            except ValueError:

                print(
                    "日付の入力形式が正しくありません。"
                )
                return

            if selected_outbound not in outbound_dates:

                print(
                    "選択した日付が往路の"
                    "空席候補に含まれていません。"
                )
                return

            # ==================================
            # STEP 5：日付差を照合
            # ==================================

            candidates = find_roundtrip_candidates(
                [selected_outbound],
                return_dates
            )

            print()
            print(
                "===== 往復候補の判定結果 ====="
            )

            if candidates:

                for candidate in candidates:

                    print()
                    print(
                        "候補発見："
                        f"{candidate['往路出発日']}"
                        " → "
                        f"{candidate['復路出発日']}"
                    )

                    print(
                        f"日付差："
                        f"{candidate['日付差']}日"
                    )

            else:

                print(
                    "4～5日差の候補はありません。"
                )

            # ==================================
            # STEP 6：CSV保存
            # ==================================

            save_candidates(candidates)

            print()
            print(
                "※ 空席はカレンダー表示のみ確認済み。"
            )
            print(
                "※ 2名分の予約可否と現地宿泊数は"
                "未確認です。"
            )

            print()
            print("処理が完了しました。")

        finally:

            await context.close()


if __name__ == "__main__":
    asyncio.run(main())
