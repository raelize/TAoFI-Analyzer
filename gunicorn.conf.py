import os

# Server socket
bind = f"{os.getenv('ANALYZER_IP', '127.0.0.1')}:{os.getenv('ANALYZER_PORT', '8000')}"
backlog = 2048

# Worker processes
# One process with threads: loaded databases are cached per process, so each
# extra worker would load (and keep) its own copy of a database in memory.
workers = 1
worker_class = "gthread"
worker_connections = 1000
threads = 8
timeout = 120
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "analyzer"

# Server mechanics
daemon = False
pidfile = "/tmp/analyzer.pid"
user = None
group = None
tmp_upload_dir = None

# SSL (not implemented)
# keyfile = '/path/to/keyfile'
# certfile = '/path/to/certfile'
