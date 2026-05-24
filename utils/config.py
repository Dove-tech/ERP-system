import os
import sys

from dotenv import load_dotenv

# 加载.env文件中的环境变量
load_dotenv()



# 从环境变量中读取配置，如果不存在则使用默认值
milvus_uri = os.getenv("milvus_uri", "http://localhost:19530")
milvus_db_name = os.getenv("milvus_db_name", "tool_db")
model_top_p = float(os.getenv("model_top_p", "0.01"))
mongo_host = os.getenv("mongo_host", "127.0.0.1")
mongo_db = os.getenv("mongo_db", "tools")
mongo_port = int(os.getenv("mongo_port", "27017"))
api_result_max_length = int(os.getenv("api_result_max_length", "30000"))
api_result_max_threshold = float(os.getenv("api_result_max_threshold", "0.1"))
model_path = os.getenv("model_path", "model")
model_name = os.getenv("model_name", "deepseek-v3")
model_temperature = float(os.getenv("model_temperature", "0.01"))
topK = int(os.getenv("topK", "5"))
model_api_key = os.getenv("model_api_key")
model_base_url = os.getenv("model_base_url")
sim_api_key = os.getenv("sim_api_key")
SECRET_KEY = os.getenv('SECRET_KEY', 'zhipocopilot@zhipo.com')
JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')

local_mode = int(os.getenv("local_mode", "1"))
if local_mode:
    milvus_uri = "http://localhost:19530"
    mongo_host = "127.0.0.1"
if  not model_api_key:
    model_api_key = os.getenv("DASHSCOPE_API_KEY")
if  not model_base_url:
    model_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
if not (sim_api_key and  model_api_key and model_base_url):
    print(f"缺乏必要的参数sim_api_key：{sim_api_key}、model_api_key：{model_api_key}、model_base_url：{model_base_url}，服务中止！")
    sys.exit()