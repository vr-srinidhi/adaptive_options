"""
Application configuration via environment variables.
"""
import os


class Settings:
    SECRET_KEY: str = os.getenv("SECRET_KEY", "CHANGE_ME_IN_PRODUCTION_USE_OPENSSL_RAND")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

    # Comma-separated list of allowed CORS origins
    ALLOWED_ORIGINS: str = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")

    # Fernet key for encrypting broker access tokens.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Store in Railway / .env as BROKER_TOKEN_ENCRYPTION_KEY
    BROKER_TOKEN_ENCRYPTION_KEY: str = os.getenv("BROKER_TOKEN_ENCRYPTION_KEY", "")

    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")


settings = Settings()
