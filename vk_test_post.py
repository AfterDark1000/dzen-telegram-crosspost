"""Publish one clearly labeled VK test post (only on explicit workflow dispatch)."""
import json
import os
import urllib.parse
import urllib.request

token = os.environ["VK_ACCESS_TOKEN"]
params = {
    "owner_id": "-242308928",
    "from_group": "1",
    "message": "🔧 Проверка автопубликации «Хроники Пикирующего ИИ».\n\nЕсли вы видите эту запись, GitHub успешно отправил её через API ВКонтакте. После проверки запись можно удалить.",
    "signed": "0",
    "access_token": token,
    "v": "5.199",
}
req = urllib.request.Request("https://api.vk.com/method/wall.post", data=urllib.parse.urlencode(params).encode("utf-8"))
with urllib.request.urlopen(req, timeout=30) as r:
    response = json.load(r)
if "error" in response:
    e = response["error"]
    print("VK API error:", e.get("error_code"), e.get("error_msg"))
    raise SystemExit(1)
post_id = response.get("response", {}).get("post_id")
if not post_id:
    raise SystemExit("No post_id returned; publication unconfirmed")
print("VK test post published:", "https://vk.com/wall-242308928_" + str(post_id))
