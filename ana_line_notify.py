
import csv
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from dotenv import load_dotenv


# ==========================================
# 基本設定
# ==========================================

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
CSV_PATH = BASE_DIR / "ana_candidates.csv"

LINE_API_URL = "https://api.line.me/v2/bot/message/push"

load_dotenv(ENV_PATH)


# ==========================================
# CSVの読み込み
# ==========================================

def load_candidates():

    if not CSV_PATH.exists():
        raise FileNotFoundError(
            "ana_candidates.csv が見つかりません。"
        )

    with open(
        CSV_PATH,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        reader = csv.DictReader(file)
        return list(reader)


# ==========================================
# LINE通知文の作成
# ==========================================

def create_message(candidates):

    lines = [
        "【ANA特典航空券・検索テスト】",
        "",
        "成田 ⇔ オークランド",
        "大人2名／エコノミー",
        "",
    ]

    if not candidates:
        lines.append("保存された候補はありません。")

    else:
        lines.append(
            f"日付条件に合う候補：{len(candidates)}件"
        )

        for index, candidate in enumerate(
            candidates,
            start=1
        ):

            lines.extend([
                "",
                f"候補 {index}",
                f"往路：{candidate['往路出発日']}",
                f"復路：{candidate['復路出発日']}",
                f"日付差：{candidate['出発日の差']}日",
                f"確認状況：{candidate['確認状況']}",
            ])

    lines.extend([
        "",
        "※ 過去の画面情報を使ったテスト通知です。",
        "※ 現在の空席・予約可能性は未確認です。",
        "※ 現地宿泊数・便の詳細も未確認です。",
    ])

    return "\n".join(lines)


# ==========================================
# LINEへ送信
# ==========================================

def send_line_message(message):

    token = os.getenv(
        "LINE_CHANNEL_ACCESS_TOKEN",
        ""
    ).strip()

    user_id = os.getenv(
        "LINE_USER_ID",
        ""
    ).strip()

    if not token:
        raise ValueError(
            ".envにLINE_CHANNEL_ACCESS_TOKENがありません。"
        )

    if not user_id or not user_id.startswith("U"):
        raise ValueError(
            ".envのLINE_USER_IDを確認してください。"
        )

    payload = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": message
            }
        ]
    }

    request = Request(
        LINE_API_URL,
        data=json.dumps(
            payload,
            ensure_ascii=False
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urlopen(request, timeout=20) as response:

            if response.status == 200:
                print("LINE APIへの送信に成功しました。")
                return True

            print(
                f"LINE API応答：{response.status}"
            )
            return False

    except HTTPError as error:

        print(
            f"LINE APIエラー：HTTP {error.code}"
        )
        print(
            "トークン、ユーザーID、"
            "友だち追加の状態を確認してください。"
        )
        return False

    except URLError as error:

        print(
            "LINE APIへの接続に失敗しました。"
        )
        print(str(error.reason))
        return False


# ==========================================
# メイン処理
# ==========================================

def main():

    print()
    print("===== ANA候補・LINE通知テスト =====")
    print()

    try:
        candidates = load_candidates()

        print(
            f"CSV読み込み成功：{len(candidates)}件"
        )

        message = create_message(candidates)

        print()
        print("送信するメッセージ：")
        print("------------------------------")
        print(message)
        print("------------------------------")

        print()
        answer = input(
            "この内容をLINEに送りますか？ (y/n)："
        ).strip().lower()

        if answer != "y":
            print("送信をキャンセルしました。")
            return

        if send_line_message(message):
            print()
            print("スマホのLINEを確認してください。")
        else:
            print()
            print("送信できませんでした。")

    except (FileNotFoundError, ValueError, KeyError) as error:
        print(f"エラー：{error}")

    print()
    print("処理が終了しました。")


if __name__ == "__main__":
    main()
