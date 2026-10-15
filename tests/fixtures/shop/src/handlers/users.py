from services import users_svc

def get_user(req):
    authenticate(req)
    return users_svc.find(req.id)
