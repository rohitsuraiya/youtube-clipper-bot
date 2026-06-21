import os
from dotenv import load_dotenv
load_dotenv("config.env")
openai_key = os.getenv("OPENAI_API_KEY")
nvidia_key = os.getenv("NVIDIA_API_KEY")
print(f"OpenAI: {openai_key[:10] if openai_key else 'NONE'}...")
print(f"NVIDIA: {nvidia_key[:10] if nvidia_key else 'NONE'}...")

if openai_key:
    from openai import OpenAI
    client = OpenAI(api_key=openai_key)
    resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":"Say OK"}])
    print(f"OpenAI GPT: {resp.choices[0].message.content}")

if nvidia_key:
    from openai import OpenAI
    client = OpenAI(api_key=nvidia_key, base_url="https://integrate.api.nvidia.com/v1")
    resp = client.chat.completions.create(model="meta/llama-3.1-8b-instruct", messages=[{"role":"user","content":"Say OK"}])
    print(f"NVIDIA LLaMA: {resp.choices[0].message.content}")
