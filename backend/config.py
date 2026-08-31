"""
配置管理模块
从 .env 文件和环境变量中加载配置，API Key 等敏感信息绝不硬编码。
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 backend/.env 文件
_env_path = Path(__file__).parent / ".env"
load_dotenv(_env_path)


class Config:
    """全局配置，所有敏感凭据均从环境变量读取。"""

    # DeepSeek API
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    # Agent 运行参数
    MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "1000"))
    COMMAND_TIMEOUT: int = int(os.getenv("COMMAND_TIMEOUT", "30"))  # 秒
    MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE", str(1024 * 1024)))  # 1MB
    COMPRESS_THRESHOLD: int = int(os.getenv("COMPRESS_THRESHOLD", "30"))  # 触发压缩的消息数
    COMPRESS_COUNT: int = int(os.getenv("COMPRESS_COUNT", "5"))  # 每次压缩的消息数

    @classmethod
    def validate(cls) -> bool:
        """检查 API Key 是否已正确配置。"""
        key = cls.DEEPSEEK_API_KEY
        return bool(key) and key != "在这里填入你的API_KEY" and key != "sk-xxx"

    @classmethod
    def is_configured(cls) -> dict:
        """返回配置状态信息（不暴露完整 Key）。"""
        key = cls.DEEPSEEK_API_KEY
        if not key or key in ("在这里填入你的API_KEY", "sk-xxx"):
            return {"configured": False, "message": "API Key 未配置，请编辑 backend/.env 文件"}
        masked = key[:6] + "..." + key[-4:] if len(key) > 10 else "***"
        return {
            "configured": True,
            "api_key_masked": masked,
            "model": cls.DEEPSEEK_MODEL,
            "base_url": cls.DEEPSEEK_BASE_URL,
        }
