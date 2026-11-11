FROM frappe/erpnext@sha256:26f105aecd431df81e6d11dda0beb68f2cab822f07074aad3fd957d5626c0a06
# Build from the checked-out ERPNext revision, retaining the matching framework/assets runtime.
COPY --chown=frappe:0 erpnext /home/frappe/frappe-bench/apps/erpnext/erpnext
COPY --chown=frappe:0 pyproject.toml /home/frappe/frappe-bench/apps/erpnext/pyproject.toml
COPY --chown=frappe:0 extensions/contract_ops /home/frappe/frappe-bench/apps/contract_ops
RUN bench pip install --no-deps -e apps/contract_ops
RUN ln -sfn /home/frappe/frappe-bench/apps/contract_ops/contract_ops/public /home/frappe/frappe-bench/assets/contract_ops
