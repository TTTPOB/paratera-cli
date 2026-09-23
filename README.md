# Paratera CLI（个人使用，未验证线上）

基于异步签名客户端和 `ParateraAPI.call` 的容器命令行。**目前仅完成离线测试；OpenAPI 入口线上未核实，不能据此认定命令已能连通生产环境。** 不使用旧版 2023 SDK。

```bash
uv sync
uv run paratera --help
# 可选：安装为独立命令
uv tool install .
```

凭据默认从 `~/.local/share/creds/paratera` 读取：

```text
PARATERA_ACCESS_KEY=your-access-key
PARATERA_SECRET_KEY=your-secret-key
```

环境变量 `PARATERA_ACCESS_KEY`、`PARATERA_SECRET_KEY` 分别优先于文件；可用 `--credentials-file PATH` 改路径。默认 API URL 是 `https://ai.blsc.cn`，可用全局 `--base-url URL` 覆盖；不要在命令行参数或版本库里记录真实密钥。

## 常用命令

```bash
uv run paratera zones
uv run paratera types --zone cn-zhongwei-ac
uv run paratera availability --zone cn-zhongwei-ac  # 一次批量查全部该区规格库存
uv run paratera images
uv run paratera get --json
uv run paratera get --id INSTANCE_UUID
uv run paratera quote --zone ZONE --name NAME --model MODEL --image IMAGE_UUID
uv run paratera create --zone ZONE --name NAME --model MODEL --image IMAGE_UUID
uv run paratera ssh --id INSTANCE_UUID
uv run paratera endpoints --id INSTANCE_UUID
uv run paratera power off --id INSTANCE_UUID
uv run paratera power on --id INSTANCE_UUID
uv run paratera power reboot --id INSTANCE_UUID
uv run paratera jobs get JOB_UUID
uv run paratera delete --id INSTANCE_UUID --yes
```

`get` 分页参数为 `--page` / `--page-size`。需要唯一实例的命令可省略 `--id`，但**仅当账户里恰好一个匹配实例**时才自动选择；多个必须指定 `--id`，可加 `--zone`。`ssh` 只打印正确引用的 `ssh -p PORT USER@HOST` 命令，`ssh --json` 返回包含 `command` 和脱敏 `endpoint` 的 JSON，**不会自动连接**。默认不会打印 SSH 密码、URL 中认证信息或 Jupyter token；确有需要使用 `ssh --show-secrets` 或 `endpoints --show-secrets`。`--json` 仍默认递归脱敏。

`create` 和 `quote` 均要求 `--zone`、`--name`、`--model`、`--image`，默认 `--billing-type PostPaid --count 1`；包月使用 `--billing-type PrePaid --pay-period MONTHS`。**创建可能冻结/扣除余额**；建议先 `quote` 再自行决定是否 `create`，命令不会强制交互。询价文档的请求体未给出完整示例，目前 CLI 传入与创建相同参数，尚未线上验证。

`power off` 默认 `saveEnv=true` 保留环境；只有显式 `--discard-env` 才传 `false`，可能删除文件和配置。按需计费默认 `STOP_CHARGING`（停机释放计算资源、后续可能无法开机），包月自动用 `KEEP_CHARGING`（关机仍计费）。可显式 `--stopped-mode STOP_CHARGING|KEEP_CHARGING`，但与查询到的计费类型冲突会报错。`delete` 必须 `--yes` 才发请求。创建、开关机、重启和删除都是**异步任务**：返回的 `jobUuid` 只表示已提交，不表示执行成功；用 `jobs get JOB_UUID` 单次查询 `done` / `success`，命令不会自动轮询。

## 底层接口和 SDK

```bash
uv run paratera api list
uv run paratera api call ack_job.DescribeJobs --params '{"jobUuid":"JOB_UUID"}' --json
uv run paratera api call ack_job.DescribeJobs --params @request.json
```

`api list` 展示 `OPERATIONS` 注册表；`api call` 通过注册表调用对应 `service.Action`，参数必须是 JSON 对象。完整官方接口覆盖由注册表与通用 `call` 提供；高频容器命令只是便捷封装，未虚构未公开 EIP 等操作。

```python
import asyncio
from paratera_cli.client import ParateraClient
from paratera_cli.api import ParateraAPI

async def example():
    async with ParateraClient() as client:
        api = ParateraAPI(client)
        data = await api.call("ackcs.DescribeServices", {"pageNum": 1, "pageSize": 100})
        print(data["rows"])

asyncio.run(example())
```

**已知文档冲突及验证边界：** [公共镜像文档](https://ai.paratera.com/document/openapi/container/production/DescribeACKPublicImages) 标示操作 `ack_product.DescribeACKPublicImages`，却将示例路径写为 `/v3/product/DescribeImages`；注册表当前采用候选、未验证的 `/v3/ack_product/DescribeACKPublicImages`，CLI 不做自动 fallback。官方 [实例接口](https://ai.paratera.com/document/openapi/container/services/DescribeServices)、[SSH 接口](https://ai.paratera.com/document/openapi/container/services/DescribeServicesSSH) 与 [任务接口](https://ai.paratera.com/document/openapi/container/jobs/DescribeJobs) 是离线载荷依据，不等于生产连通性验证。

```bash
uv run pytest tests/test_cli.py
```
