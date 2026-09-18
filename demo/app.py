"""Demo web service that connects to PostgreSQL and simulates API traffic.

Runs as a long-lived process inside ECS Fargate. Logs realistic application
output to stdout, which CloudWatch captures automatically. When the database
becomes unreachable (e.g. security group revoked), it logs real connection
errors that Beacon can analyze.
"""

import json
import logging
import os
import random
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("demo-webapp")

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "demo")
DB_USER = os.environ.get("DB_USER", "demo")
DB_PASS = os.environ.get("DB_PASSWORD", "demo")

ENDPOINTS = [
    ("GET", "/api/v2/orders", "orders-service"),
    ("POST", "/api/v2/payments", "payments-service"),
    ("GET", "/api/v2/users/profile", "users-service"),
    ("POST", "/api/v2/inventory/reserve", "inventory-service"),
    ("GET", "/api/v2/recommendations", "recommendations-service"),
]

db_healthy = True
consecutive_failures = 0


def _get_connection():
    import psycopg2

    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        connect_timeout=5,
    )


def _init_db():
    """Create the demo table if it doesn't exist."""
    try:
        conn = _get_connection()
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS orders ("
            "  id SERIAL PRIMARY KEY,"
            "  customer TEXT NOT NULL,"
            "  total NUMERIC(10,2) NOT NULL,"
            "  created_at TIMESTAMP DEFAULT NOW()"
            ")"
        )
        cur.execute(
            "INSERT INTO orders (customer, total) "
            "SELECT 'seed-user-' || g, (random() * 500)::numeric(10,2) "
            "FROM generate_series(1, 50) g "
            "WHERE NOT EXISTS (SELECT 1 FROM orders LIMIT 1)"
        )
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Database initialized. Host=%s:%s db=%s", DB_HOST, DB_PORT, DB_NAME)
    except Exception:
        logger.exception("Failed to initialize database")


def _simulate_traffic():
    """Run one round of simulated API traffic against the database."""
    global db_healthy, consecutive_failures

    method, path, service = random.choice(ENDPOINTS)
    start = time.monotonic()

    try:
        conn = _get_connection()
        cur = conn.cursor()

        if "orders" in path:
            cur.execute(
                "SELECT id, customer, total FROM orders "
                "ORDER BY created_at DESC LIMIT 10"
            )
            cur.fetchall()
        elif "payments" in path:
            cur.execute(
                "INSERT INTO orders (customer, total) VALUES (%s, %s) RETURNING id",
                (
                    f"cust-{random.randint(1000, 9999)}",
                    round(random.uniform(10, 500), 2),
                ),
            )
            conn.commit()
        elif "users" in path:
            cur.execute("SELECT COUNT(*) FROM orders")
            cur.fetchone()
        elif "inventory" in path:
            cur.execute(
                "UPDATE orders SET total = total + 1 WHERE id = "
                "(SELECT id FROM orders ORDER BY random() LIMIT 1)"
            )
            conn.commit()
        else:
            cur.execute("SELECT 1")

        cur.close()
        conn.close()

        elapsed_ms = int((time.monotonic() - start) * 1000)
        consecutive_failures = 0
        db_healthy = True
        logger.info(
            "%s %s 200 %dms - service=%s db_pool=23/100 db_host=%s region=us-east-1",
            method,
            path,
            elapsed_ms,
            service,
            DB_HOST,
        )

    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        consecutive_failures += 1
        err_msg = str(exc).replace("\n", " ").strip()

        if consecutive_failures <= 2:
            logger.warning(
                "ConnectionPool WARNING: pool utilization rising. "
                "db_host=%s query_timeout=%dms error='%s' "
                "service=%s region=us-east-1",
                DB_HOST,
                elapsed_ms,
                err_msg,
                service,
            )
        elif consecutive_failures <= 5:
            db_healthy = False
            logger.error(
                "%s %s 503 %dms - service=%s "
                "error='connection_timeout' db_host=%s "
                "db_pool=EXHAUSTED waited=%dms region=us-east-1",
                method,
                path,
                elapsed_ms,
                service,
                DB_HOST,
                elapsed_ms,
            )
        else:
            db_healthy = False
            logger.error(
                "CRITICAL: Database unreachable. Host=%s:%s "
                "consecutive_failures=%d last_error='%s' "
                "affected_services=orders-service,payments-service,"
                "inventory-service error_rate=100%% region=us-east-1",
                DB_HOST,
                DB_PORT,
                consecutive_failures,
                err_msg,
            )

        if consecutive_failures == 5:
            logger.error(
                "HealthCheck FAILED: /health returned 503. "
                "db_primary=unreachable db_host=%s "
                "api_latency_p99=%dms (threshold: 500ms) "
                "error_rate=100%% (threshold: 1%%)",
                DB_HOST,
                elapsed_ms,
            )


class HealthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for ECS health checks."""

    def do_GET(self):  # noqa: N802
        if db_healthy:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"status": "healthy"}).encode())
        else:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(
                json.dumps({"status": "unhealthy", "db": "unreachable"}).encode()
            )

    def log_message(self, format, *args):  # noqa: A002
        pass


def _run_health_server():
    server = HTTPServer(("0.0.0.0", 8080), HealthHandler)
    server.serve_forever()


def main():
    logger.info("Starting demo web service. DB_HOST=%s DB_PORT=%s", DB_HOST, DB_PORT)

    health_thread = Thread(target=_run_health_server, daemon=True)
    health_thread.start()
    logger.info("Health check server listening on :8080")

    time.sleep(3)
    _init_db()

    logger.info(
        "Service ready. Simulating API traffic every 3s. "
        "To trigger an incident, revoke the RDS security group rule."
    )

    while True:
        _simulate_traffic()
        time.sleep(random.uniform(2.0, 4.0))


if __name__ == "__main__":
    main()
