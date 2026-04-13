import requests
import os
import sys
import zipfile
from pathlib import Path

# 替换为你的 Worker 地址
API_URL = "https://photo-api-v2.3412334014.workers.dev/upload"

def upload_file(file_path):
    """上传单个文件（图片或zip）"""
    with open(file_path, 'rb') as f:
        files = {'file': (Path(file_path).name, f, 'image/jpeg')}
        try:
            r = requests.post(API_URL, files=files)
            print(f"上传成功: {file_path} -> {r.json()}")
        except Exception as e:
            print(f"上传失败: {file_path} - {e}")

def upload_folder(folder_path):
    """将文件夹内所有图片打包成 ZIP 并上传"""
    zip_name = f"{Path(folder_path).name}.zip"
    # 创建 ZIP 文件，包含所有图片和 JSON 文件
    with zipfile.ZipFile(zip_name, 'w') as zipf:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                # 只要图片和 JSON 文件
                if file.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.json')):
                    full_path = os.path.join(root, file)
                    # 保持文件名，不保留目录结构（可根据需要调整）
                    zipf.write(full_path, arcname=file)
    # 上传 ZIP 文件
    with open(zip_name, 'rb') as f:
        files = {'file': (zip_name, f, 'application/zip')}
        try:
            r = requests.post(API_URL, files=files)
            print(f"📦 文件夹已打包上传: {zip_name} -> {r.json()}")
        except Exception as e:
            print(f"❌ 上传失败: {zip_name} - {e}")
    # 删除临时 ZIP
    os.remove(zip_name)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python upload.py <文件或文件夹路径>")
        sys.exit(1)
    target = sys.argv[1]
    if os.path.isfile(target):
        upload_file(target)
    elif os.path.isdir(target):
        upload_folder(target)
    else:
        print("路径无效")