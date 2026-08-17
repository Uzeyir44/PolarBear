"""
Pydantic schemas for login and tokens.

LoginRequest is the input: a username OR email, plus a password (so one
field lets users log in either way). Token is the response: the JWT plus
a fixed type label the client echoes back in the Authorization header.
"""
from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"