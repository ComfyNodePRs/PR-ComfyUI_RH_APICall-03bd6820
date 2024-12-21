import requests
import time
import json
from PIL import Image
from io import BytesIO
import numpy as np
import torch

class ExecuteNode:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "apiConfig": ("STRUCT",),  # 设置节点的输入
                "nodeInfoList": ("ARRAY", {"default": []}),  # NodeInfoList节点的输出
            },
        }

    RETURN_TYPES = ("IMAGE", )  # 仅定义返回类型为 IMAGE
    CATEGORY = "RunningHub"
    FUNCTION = "process"  # 指向 process 方法

    def process(self, apiConfig, nodeInfoList):
        """
        该节点通过调用 RunningHub API 创建任务并返回生成的图片链接。
        """
        # 打印请求数据，方便调试
        print(f"API request data: {apiConfig}")
        print(f"Node info list: {nodeInfoList}")

        # 1. 查询账户状态，检查是否可以提交任务
        account_status = self.check_account_status(apiConfig["apiKey"])
        if int(account_status["currentTaskCounts"]) > 0:
            print("There are tasks running, waiting for them to finish.")
            # 等待最多 10 分钟，如果任务未完成，则超时
            start_time = time.time()
            while account_status["currentTaskCounts"] > 0 and time.time() - start_time < 600:
                time.sleep(2)  # 每 2 秒查询一次
                account_status = self.check_account_status(apiConfig["apiKey"])
            if int(account_status["currentTaskCounts"]) > 0:
                raise Exception("Timeout: There are still running tasks after 10 minutes.")

        # 2. 创建任务
        task_creation_result = self.create_task(apiConfig, nodeInfoList)
        if task_creation_result["code"] != 0:
            raise Exception(f"Task creation failed: {task_creation_result['msg']}")

        task_id = task_creation_result["data"]["taskId"]
        task_status = task_creation_result["data"]["taskStatus"]
        print(f"Task created successfully, taskId: {task_id}, status: {task_status}")

        # 3. 查询任务状态直到任务完成
        while task_status != "success":
            print(f"Task still running, checking again in 2 seconds...")
            time.sleep(2)  # 每 2 秒检查一次任务状态
            task_status_result = self.check_task_status(task_id, apiConfig["apiKey"])
            print(f"Task info, taskId: {task_id}, status: {task_status_result}")
            task_status = task_status_result.get("taskStatus", "unknown")  # 从结果中获取任务状态
            if task_status != "RUNNING":
                print(f"Task failed or completed with status: {task_status}")
                break

        # 4. 任务完成，处理输出
        return self.process_task_output(task_id, apiConfig["apiKey"])

    def check_account_status(self, api_key):
        """
        查询账户状态，检查是否可以提交新任务
        """
        url = "https://www.runninghub.cn/uc/openapi/accountStatus"
        headers = {
            "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
            "Content-Type": "application/json",
        }
        data = {
            "apikey": api_key
        }

        response = requests.post(url, json=data, headers=headers)
        result = response.json()
        if result["code"] != 0:
            raise Exception(f"Failed to get account status: {result['msg']}")
        # 检查并确保 currentTaskCounts 是整数
        try:
            current_task_counts = int(result["data"]["currentTaskCounts"])
        except ValueError:
            raise Exception("Invalid value for currentTaskCounts. It should be an integer.")

        result["data"]["currentTaskCounts"] = current_task_counts
        return result["data"]

    def create_task(self, apiConfig, nodeInfoList):
        """
        创建任务
        """
        url = "https://www.runninghub.cn/task/openapi/create"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
        }
        data = {
            "workflowId": apiConfig["workflowId"],
            "apiKey": apiConfig["apiKey"],
            "nodeInfoList": [
                {
                    "nodeId": int(nodeInfo["nodeId"]),  # 确保 nodeId 为整数类型
                    "fieldName": nodeInfo["fieldName"],
                    "fieldValue": nodeInfo["fieldValue"],
                }
                for nodeInfo in nodeInfoList
            ],
        }

        response = requests.post(url, json=data, headers=headers)
        return response.json()

    def check_task_status(self, task_id, api_key):
        """
        查询任务状态
        """
        url = "https://www.runninghub.cn/task/openapi/outputs"
        headers = {
            "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
            "Content-Type": "application/json",
        }
        data = {
            "taskId": task_id,
            "apiKey": api_key
        }

        response = requests.post(url, json=data, headers=headers)
        
        # 打印响应以便调试
        print("Response Status Code:", response.status_code)
        try:
            response_json = response.json()
            print("Response JSON:", json.dumps(response_json, indent=4, ensure_ascii=False))
        except ValueError:
            print("Response Text:", response.text)

        if response.status_code != 200:
            raise Exception(f"HTTP request failed with status code: {response.status_code}")

        result = response.json()
        
        # 检查 'code' 和 'msg' 字段
        if result.get("code") != 0:
            # 如果任务正在运行，返回一个特定的状态
            if result.get("msg") == "APIKEY_TASK_IS_RUNNING":
                return {"taskStatus": "RUNNING"}
        # 检查 'data' 是否存在并且是列表类型
        if result.get("data") and isinstance(result["data"], list):
            if len(result["data"]) > 0:
                return result["data"][0]  # 假设列表中的第一个元素
            else:
                return {"taskStatus": "RUNNING"}  # 如果 data 是空列表，任务仍在运行
        else:
            return {"taskStatus": "RUNNING"}  # 如果 data 是 None，任务仍在运行

    def process_task_output(self, task_id, api_key):
        """
        处理任务输出，返回文件链接。
        """
        task_status_result = self.check_task_status(task_id, api_key)
        
        # 记录任务状态结果以了解其结构
        print("Task Status Result:", json.dumps(task_status_result, indent=4, ensure_ascii=False))
        
        image_urls = []
        
        # 确保 task_status_result 是字典类型
        if isinstance(task_status_result, dict):
            # 检查 fileUrl 和 fileType
            file_url = task_status_result.get("fileUrl")
            file_type = task_status_result.get("fileType")
            if file_url and file_type.lower() in ["png", "jpg", "jpeg"]:
                image_urls.append(file_url)  # 添加到 images 列表
        elif isinstance(task_status_result, list):
            for output in task_status_result:
                if isinstance(output, dict):
                    file_url = output.get("fileUrl")
                    file_type = output.get("fileType")
                    if file_url and file_type.lower() in ["png", "jpg", "jpeg"]:
                        image_urls.append(file_url)  # 添加到 images 列表

        if not image_urls:
            raise Exception("No valid image output found.")
        
        # 假设只有一张图，可以根据需要扩展
        image_data = None
        if image_urls:
            print("Downloading image from URL:", image_urls[0])  # 记录图像 URL
            image_data = self.download_image(image_urls[0])  # 下载并处理图像
            print("Image downloaded and processed successfully.")
        
        return (image_data, )  # 返回一个元组，匹配 RETURN_TYPES

    def download_image(self, image_url):
        """
        从 URL 下载图像并转换为适合预览或保存的 torch.Tensor 格式。
        """
        response = requests.get(image_url)
        if response.status_code == 200:
            img = Image.open(BytesIO(response.content)).convert("RGB")
            img_array = np.array(img).astype(np.float32) / 255.0  # 归一化到 [0, 1]
            img_tensor = torch.from_numpy(img_array).unsqueeze(0)  # 形状 (1, H, W, C)
            img_tensor = img_tensor.contiguous()
            
            # 打印图像尺寸
            print(f"Downloaded image dimensions: {img_tensor.shape}")  # 打印图像形状
            
            return img_tensor
        else:
            raise Exception(f"Failed to download image: {image_url}")

    def download_video(self, video_url):
        """
        从 URL 下��视频。
        根据 ComfyUI 的要求实现此方法。
        """
        response = requests.get(video_url, stream=True)
        if response.status_code == 200:
            # 示例：将视频保存到临时位置并返回路径或数据
            video_content = response.content
            # 您可能需要根据 ComfyUI 的要求处理视频数据
            # 目前，返回原始字节
            return video_content
        else:
            raise Exception(f"Failed to download video: {video_url}")