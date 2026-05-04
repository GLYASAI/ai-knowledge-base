from tests.security import filter_output

text = "联系作者 13812345678 或 author@example.com 获取完整代码 · IP 192.168.1.1"
filtered, detections = filter_output(text, mask=True)
print(f"原文：{text}")
print(f"掩码：{filtered}")
print(f"检出：{detections}")