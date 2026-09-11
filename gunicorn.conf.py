import os
bind = '0.0.0.0:' + os.environ.get('PORT','8080')
workers = 1
worker_class = 'gthread'
threads = 8
timeout = 30
graceful_timeout = 10
limit_request_line = 2048
limit_request_fields = 32
limit_request_field_size = 4096
accesslog = None
errorlog = '-'
