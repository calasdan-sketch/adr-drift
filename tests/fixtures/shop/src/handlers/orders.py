from db.drivers import pg  # VIOLATION: handlers -> db.drivers

def create_order(req):
    return pg.query("INSERT INTO orders ...")  # NOTE: no auth check here
