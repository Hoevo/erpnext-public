#!/usr/bin/env bash
# Disposable restored v17 site only. Never point this Compose project at the v16 data root.
set -euo pipefail
cd /home/frappe/frappe-bench
test -f /restore/database.sql.gz
test ! -f sites/erp.merged-test.localhost/site_config.json
printf 'frappe\nerpnext\ncontract_ops\n' > sites/apps.txt
test -f sites/common_site_config.json || printf '{}\n' > sites/common_site_config.json
test -e sites/assets || ln -s /home/frappe/frappe-bench/assets sites/assets
bench set-config -g db_host db
bench set-config -gp db_port 3306
bench set-config -g redis_cache redis://cache:6379
bench set-config -g redis_queue redis://queue:6379
bench set-config -g redis_socketio redis://queue:6379
bench set-config -gp socketio_port 9000
bench set-config -g webserver_host backend
bench set-config -gp webserver_port 8000
bench set-config -gp socketio_worker_threads 1
bench new-site erp.merged-test.localhost --db-host db --db-root-username root \
  --db-root-password "${DB_ROOT_PASSWORD:?Required}" --admin-password "${ADMIN_PASSWORD:?Required}" \
  --mariadb-user-host-login-scope '%' --source-sql /restore/database.sql.gz
bench --site erp.merged-test.localhost set-config disable_scheduler 1
bench --site erp.merged-test.localhost set-config mute_emails 1
bench --site erp.merged-test.localhost set-config allow_tests 1
bench --site erp.merged-test.localhost set-config host_name http://127.0.0.1:8261
for archive in files.tar private-files.tar; do
  if [[ -f /restore/$archive ]]; then
    tar -xf "/restore/$archive" -C sites/erp.merged-test.localhost
  fi
done
bench --site erp.merged-test.localhost execute contract_ops.tests.preservation.snapshot --kwargs '{"phase":"before"}'
bench --site erp.merged-test.localhost migrate
bench --site erp.merged-test.localhost clear-cache
bench --site erp.merged-test.localhost execute contract_ops.tests.preservation.snapshot --kwargs '{"phase":"after"}'
