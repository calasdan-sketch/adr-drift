from db.drivers import pg  # fine: services may use drivers

def create(data):
    return pg.query("INSERT INTO orders ...")
