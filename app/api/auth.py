from fastapi import APIRouter, Request, Response

from app.deps import CurrentUser, Db, start_session
from app.schemas import LoginRequest, LoginResponse, UserOut
from app.services import users

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Db):
    user = users.authenticate(db, payload.username, payload.password)
    start_session(request, user)
    return {"user": user}


@router.post("/logout", status_code=204)
def logout(request: Request):
    request.session.clear()
    return Response(status_code=204)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user
