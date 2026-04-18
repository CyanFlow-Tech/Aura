from fastapi import Security, HTTPException, status, Query
from fastapi.security.api_key import APIKeyHeader
from config import config


api_key_header = APIKeyHeader(
    name=config.api.auth_header,
    auto_error=False,
)

async def get_api_key(
    api_key_header: str = Security(api_key_header),
    token: str = Query(None)
):
    if api_key_header == config.api.auth_token:
        return api_key_header
    if token == config.api.auth_token:
        return token
        
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="无权访问"
    )
