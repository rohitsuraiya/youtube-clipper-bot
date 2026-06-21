import os, json
from dotenv import load_dotenv
load_dotenv("config.env")
from openai import OpenAI
client = OpenAI(api_key=os.getenv("NVIDIA_API_KEY"), base_url="https://integrate.api.nvidia.com/v1")
resp = client.chat.completions.create(
    model="meta/llama-3.1-8b-instruct",
    messages=[{"role": "user", "content": 'Return only this JSON array, nothing else: [{"start": 10, "end": 25, "reason": "funny"}]'}]
)
print(resp.choices[0].message.content)
