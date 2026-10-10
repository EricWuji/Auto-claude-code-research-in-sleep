#!/usr/bin/env bash
set -euo pipefail
WORK_DIR=/mnt/shared-storage-user/songdemin/user/wuyinqi
echo "=== node ==="
hostname
echo "=== GPU ==="
nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv
GPU_COUNT=$(nvidia-smi --query-gpu=index --format=csv,noheader | wc -l)
test "$GPU_COUNT" -eq 8
echo "=== mount ==="
test -d "$WORK_DIR"
cd "$WORK_DIR"
pwd
PROBE_FILE=$(mktemp "$WORK_DIR/.rjob-smoke-XXXXXX")
trap 'rm -f -- "$PROBE_FILE"' EXIT
printf 'rjob mount write check\n' > "$PROBE_FILE"
test "$(cat "$PROBE_FILE")" = 'rjob mount write check'
rm -f -- "$PROBE_FILE"
trap - EXIT
echo MOUNT_READ_WRITE_OK
echo "=== Python / PyTorch ==="
python3 --version
python3 - <<'PY'
import importlib.util
import os
print("CUDA_VISIBLE_DEVICES:", os.getenv("CUDA_VISIBLE_DEVICES", "未设置"))
if importlib.util.find_spec("torch") is None:
    print("基础镜像 Python 未安装 torch；可在后续任务中使用挂载的 conda 环境。")
else:
    import torch
    print("torch:", torch.__version__, "CUDA:", torch.version.cuda)
    print("CUDA available:", torch.cuda.is_available(), "GPU count:", torch.cuda.device_count())
    if torch.cuda.is_available():
        for i in range(torch.cuda.device_count()):
            x = torch.arange(8, device=f"cuda:{i}", dtype=torch.float32)
            assert x.square().sum().item() == 140.0
        print("TORCH_GPU_COMPUTE_OK")
PY
echo SMOKE_OK
