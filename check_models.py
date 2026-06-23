import os
import google.generativeai as genai
from dotenv import load_dotenv

# 加载 .env 中的 API Key
load_dotenv()
# 兼容 GEMINI_API_KEY 或 GOOGLE_API_KEY 两种常见命名
api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") 

if not api_key:
    print("❌ 错误：没有在 .env 文件中找到 API Key！")
    exit()

print(f"🔑 成功读取到 API Key: {api_key[:10]}... (已隐藏后半部分)")
print("🔍 正在连接谷歌服务器，拉取可用模型列表...\n")

try:
    genai.configure(api_key=api_key)
    # 拉取所有支持文本生成的模型
    available_models = []
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            available_models.append(m.name)
            print(f"✅ 你的账号可用模型: {m.name}")
    
    if not available_models:
        print("⚠️ 你的账号虽然连上了，但没有任何可用的文本生成模型权限！")
except Exception as e:
    print(f"❌ 连接 API 时发生严重错误: {e}")