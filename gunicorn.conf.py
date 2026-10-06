"""Gunicorn settings for TerraSync.  Run:  gunicorn -c gunicorn.conf.py app:app

Threads (not many processes) suit this app: requests spend most of their time
waiting on MySQL, Gemini and SMS providers. The long timeout covers a slow
Gemini triage call on report submission.
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "90"))
accesslog = "-"
errorlog = "-"
# Behind Caddy / a cloud load balancer: trust X-Forwarded-* so request.remote_addr
# (used by the OTP per-IP rate limit) and https detection are correct.
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "*")
