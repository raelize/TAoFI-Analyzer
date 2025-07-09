import os

# Server socket
bind = f"{os.getenv('ANALYZER_IP', '0.0.0.0')}:{os.getenv('ANALYZER_PORT', '9393')}"
backlog = 2048

# Worker processes
workers = 4
worker_class = "gthread"
worker_connections = 1000
threads = 2
timeout = 120
keepalive = 5

# Restart workers
max_requests = 1000
max_requests_jitter = 100

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
