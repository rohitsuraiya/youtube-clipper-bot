import os
from dotenv import load_dotenv

load_dotenv("config.env")
key = os.getenv("NVIDIA_API_KEY")
if key:
    print(f"Key starts with: {key[:15]}...")
    print(f"Length: {len(key)}")
else:
    print("NO NVIDIA KEY FOUND")
