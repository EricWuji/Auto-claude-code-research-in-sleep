"""生成共享存储启动脚本，并通过 rjob submit -- bash 提交 GPU 检查任务。"""
import argparse
import json
import re
import shlex
import subprocess
import shutil
import uuid
from datetime import datetime
from pathlib import Path


NAMESPACE = "ailab-llmagent"
# 挂载已有 songdemin 卷，并保持用户目录在容器内的绝对路径不变。
MOUNT = "gpfs://gpfs1/songdemin:/mnt/shared-storage-user/songdemin"


def positive_int(value):
    value = int(value)
    if value <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return value


def build_command(options, job, launch_script):
    args = [
        "rjob", "submit", "--name", job, "--namespace", NAMESPACE,
        "--charged-group", "llmagent_gpu", "--private-machine", "group",
        "-P", "1", "--gpu", "8", "--memory", str(options.memory), "--cpu", "128",
        "--image", "registry.h.pjlab.org.cn/ailab/ml-base:22.04-pjlab-20251117",
        "--mount", MOUNT,
    ]
    args.extend(["--", "bash", str(launch_script)])
    return args


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory", type=positive_int, default=160000,
                        help="每副本主机内存 MiB，默认保留当前配置 160000")
    parser.add_argument("--dry-run", action="store_true", help="仅显示提交命令，不提交任务、不覆盖记录")
    parser.add_argument("--output-dir", type=Path, default=Path.cwd(),
                        help="提交记录目录，默认当前工作目录")
    parser.add_argument("--run-dir", type=Path,
                        help="worker 可访问的共享运行目录，默认共享用户目录下的独立 outputs 目录")
    options = parser.parse_args()
    job = "wuyinqi-gpu-smoke-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    run_dir = (options.run_dir or Path("/mnt/shared-storage-user/songdemin/user/wuyinqi/outputs") / job).expanduser().resolve()
    launch_script = run_dir / "run.sh"
    args = build_command(options, job, launch_script)
    if options.dry_run:
        print(shlex.join(args))
        return 0

    # 每轮使用新目录，避免覆盖已提交任务的启动脚本。
    run_dir.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(Path(__file__).with_name("gpu_smoke.sh"), run_dir / "gpu_smoke.sh")
    launch_script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f"cd {shlex.quote(str(run_dir))}\n"
        "timeout --signal=TERM --kill-after=10s 120s bash ./gpu_smoke.sh 2>&1 | tee smoke.log\n"
    )
    output_dir = options.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = {"job": job, "namespace": NAMESPACE, "mount": MOUNT,
                "workdir": "/mnt/shared-storage-user/songdemin/user/wuyinqi",
                "run_dir": str(run_dir), "launch_script": str(launch_script),
                "memory_mib": options.memory, "submission_status": "pending", "argv": args}
    (output_dir / "gpu-smoke-job.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    (output_dir / "gpu-smoke-submit-command.txt").write_text(shlex.join(args) + "\n")
    print("提交任务:", job, flush=True)
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (output_dir / "gpu-smoke-submit.log").write_text(result.stdout)
    metadata["submit_exit_code"] = result.returncode
    metadata["submission_status"] = "accepted" if result.returncode == 0 else "failed"
    match = re.search(r"created rjob_name:\s*(\S+)", result.stdout)
    if match:
        metadata["metadata_name"] = match.group(1)
    (output_dir / "gpu-smoke-job.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    print(result.stdout, end="", flush=True)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
