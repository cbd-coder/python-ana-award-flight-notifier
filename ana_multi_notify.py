
import asyncio
import csv
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright

from ana_save_calendar import (
    PROFILE_DIR,
    extract_calendar,
    find_calendar_page,
)

from ana_line_notify import send_line_message


# ==========================================
# 基本設定
# ==========================================

BASE_DIR = Path(__file__).resolve().parent

OUTPUT_FILE = (
    BASE_DIR / "ana_multi_candidates.csv"
)

MIN_DAYS = 4
MAX_DAYS = 5

# 条件に合う候補があればLINEへ送信
SEND_LINE_AUTOMATICALLY = True


# ==========================================
# 入力待ち
# ==========================================

async def ask(message):
    return await asyncio.to_thread(input, message)


# ==========================================
# 年をまたぐ日付の判定
# ==========================================

def resolve_date(record, reference_date):
    """
    ANAの画面から取得した月・日について、
    基準日に最も近い年を採用する。

    例：
    往路 2027-12-30
    復路 1月3日
    → 2028-01-03
    """

    month = int(record["month"])
    day = int(record["day"])

    choices = []

    for year in range(
        reference_date.year - 1,
        reference_date.year + 2
    ):
        try:
            candidate = date(year, month, day)
            choices.append(candidate)
        except ValueError:
            continue

    if not choices:
        raise ValueError(
            f"日付を解釈できません：{month}/{day}"
        )

    return min(
        choices,
        key=lambda item: abs(
            (item - reference_date).days
        )
    )


def convert_dates(records, reference_date):

    dates = set()

    for record in records:
        dates.add(
            resolve_date(record, reference_date)
        )

    return sorted(dates)


# ==========================================
# 日付表示
# ==========================================

def show_dates(title, dates):

    print()
    print(f"===== {title} =====")

    if not dates:
        print("該当する空席表示はありません。")
        return

    for item in dates:
        print(item.isoformat())

    print(f"取得件数：{len(dates)}件")


# ==========================================
# 往復日程の照合
# ==========================================

def find_matches(outbound, return_dates):

    matches = []

    for inbound in return_dates:

        difference = (
            inbound - outbound
        ).days

        if MIN_DAYS <= difference <= MAX_DAYS:

            matches.append({
                "往路出発日": outbound.isoformat(),
                "復路出発日": inbound.isoformat(),
                "日付差": difference,
                "人数": 2,
                "クラス": "特典エコノミー",
                "確認状況": (
                    "カレンダー表示のみ・"
                    "便詳細と2名分の予約可否は未確認"
                ),
            })

    return matches


# ==========================================
# CSV保存
# ==========================================

def save_candidates(candidates):

    columns = [
        "往路出発日",
        "復路出発日",
        "日付差",
        "人数",
        "クラス",
        "確認状況",
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
    print("CSVを保存しました。")
    print(f"保存先：{OUTPUT_FILE}")


# ==========================================
# LINE通知文の作成
# ==========================================

def create_message(candidates):

    lines = [
        "【ANA特典航空券・空席候補】",
        "",
        "成田 ⇔ オークランド",
        "大人2名／特典エコノミー",
        "",
        f"候補：{len(candidates)}件",
    ]

    for index, item in enumerate(
        candidates,
        start=1
    ):

        lines.extend([
            "",
            f"■ 候補 {index}",
            f"往路：{item['往路出発日']}",
            f"復路：{item['復路出発日']}",
            f"出発日差：{item['日付差']}日",
        ])

    lines.extend([
        "",
        "※カレンダー上の候補です。",
        "※現在の予約可能性は未確認。",
        "※現地宿泊数・便名・必要マイル数も未確認。",
    ])

    message = "\n".join(lines)

    if len(message) > 4500:
        raise ValueError(
            "LINE通知が長すぎます。"
            "候補数を減らしてください。"
        )

    return message


# ==========================================
# メイン処理
# ==========================================

async def main():

    print()
    print("=================================")
    print(" ANA特典航空券・複数往路候補の照合")
    print("=================================")
    print()
    print("ANAのログイン・検索は手動です。")
    print("往路の選択も手動です。")

    # 日付の年を判定する基準
    reference_text = await ask(
        "\n検索基準の往路日付"
        "（例：2027-01-19）："
    )

    try:
        reference_date = date.fromisoformat(
            reference_text.strip()
        )
    except ValueError:
        print("日付形式が正しくありません。")
        return

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
            print("ブラウザが開きました。")
            print("ANAへ手動ログインして検索してください。")

            await ask(
                "空席カレンダーが表示されたらEnter："
            )

            page = await find_calendar_page(context)

            if page is None:
                print("空席カレンダーが見つかりません。")
                return

            # ==================================
            # STEP 1：往路候補を取得
            # ==================================

            result = await extract_calendar(page)

            outbound_dates = convert_dates(
                result["outbound"],
                reference_date
            )

            show_dates(
                "往路の空席候補",
                outbound_dates
            )

            if not outbound_dates:
                print("往路候補がありません。")
                return

            # ==================================
            # STEP 2：往路候補を順番に確認
            # ==================================

            all_candidates = []

            for index, outbound in enumerate(
                outbound_dates,
                start=1
            ):

                print()
                print("==============================")
                print(
                    f"往路候補 {index}/{len(outbound_dates)}"
                )
                print(f"日付：{outbound}")
                print("==============================")

                print()
                print(
                    "ANA画面でこの往路日を"
                    "選択してください。"
                )
                print(
                    "復路の空席表示が更新されたら"
                    "Enterを押します。"
                )

                answer = await ask(
                    "Enterで取得／sでスキップ／"
                    "qで終了："
                )

                answer = answer.strip().lower()

                if answer == "q":
                    print("残りの調査を終了します。")
                    break

                if answer == "s":
                    print("この往路をスキップしました。")
                    continue

                # 最新の復路表示を取得
                result = await extract_calendar(page)

                return_dates = convert_dates(
                    result["return"],
                    outbound + timedelta(days=4)
                )

                show_dates(
                    f"往路{outbound}に対する復路候補",
                    return_dates
                )

                matches = find_matches(
                    outbound,
                    return_dates
                )

                if matches:
                    print()
                    print(
                        f"条件一致：{len(matches)}件"
                    )
                    all_candidates.extend(matches)

                    for item in matches:
                        print(
                            item["往路出発日"],
                            "→",
                            item["復路出発日"]
                        )
                else:
                    print(
                        "4～5日差の候補はありません。"
                    )

            # ==================================
            # STEP 3：重複除去
            # ==================================

            unique = {}

            for item in all_candidates:
                key = (
                    item["往路出発日"],
                    item["復路出発日"]
                )
                unique[key] = item

            candidates = sorted(
                unique.values(),
                key=lambda item: (
                    item["往路出発日"],
                    item["復路出発日"]
                )
            )

            print()
            print("===== 最終集計 =====")
            print(
                f"条件一致：{len(candidates)}件"
            )

            # ==================================
            # STEP 4：CSV保存
            # ==================================

            save_candidates(candidates)

            # ==================================
            # STEP 5：LINE通知
            # ==================================

            if not candidates:
                print(
                    "候補なし：LINE通知は行いません。"
                )
                return

            message = create_message(candidates)

            print()
            print("===== LINE通知内容 =====")
            print(message)

            if not SEND_LINE_AUTOMATICALLY:

                answer = await ask(
                    "LINEに送りますか？ (y/n)："
                )

                if answer.strip().lower() != "y":
                    print("送信を中止しました。")
                    return

            success = send_line_message(message)

            if success:
                print()
                print("LINE通知が完了しました。")
            else:
                print()
                print("LINE通知に失敗しました。")
                print("CSVは保存されています。")

        except Exception as error:
            print()
            print("エラーが発生しました。")
            print(str(error))

        finally:
            await context.close()

    print()
    print("処理を終了しました。")


if __name__ == "__main__":
    asyncio.run(main())
