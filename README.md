# Paratera CLI（个人使用，未验证线上）

基于异步 httpx2 的容器命令行，提供独立的签名 OpenAPI 与网页登录态后端。**生产两个域名的 /v3 OpenAPI 均返回静态 405，不可用；网页后端目前为建议选项，但接口映射仍在验证，实际创建/开关机等需谨慎。** 网页映射不是 Key 签名 API，不使用旧版 2023 SDK。

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

环境变量 `PARATERA_ACCESS_KEY`、`PARATERA_SECRET_KEY` 分别优先于文件；可用 `--credentials-file PATH` 改路径。为保留既有行为，默认 `--backend openapi`，OpenAPI 默认 URL 为 `https://ai.blsc.cn`；**生产使用建议明确传 `--backend web`**，网页默认 URL 独立为 `https://ai.paratera.com`。全局 `--base-url URL` 仅在显式设置时覆盖所选后端，不要在命令行参数或版本库里记录真实密钥。

网页后端只接受**已经登录的网页会话 token**，不实现账号密码登录、验证码或自动刷新。将自己登录后获取的 token 从 stdin 导入：

```bash
# 终端中避免回显，实际 token 由安全的 stdin 管道输入；也接受 {"token":"..."} JSON
cat /path/to/private-token-or-json | uv run paratera --backend web session import
uv run paratera --backend web zones
uv run paratera --backend web api list
```

默认保存到 `~/.local/share/creds/paratera-session.json`，可用 `--session-file PATH` 指定；文件为 0600。也可设置 `PARATERA_TOKEN` 环境变量覆盖文件。网页后端发送 `token: TOKEN` 和 `Ai-Authorization: Bearer TOKEN`，不输出 token、请求头或凭据内容。会话有效期未知，服务端可能过期或注销撤销；401/100006 会提示重新登录并导入，不会自动重试认证。不要将 token 放入命令行参数、Git 或普通日志。

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

`api list` 在 OpenAPI 后端展示 `OPERATIONS` 签名接口注册表；在网页后端**仅**展示已经映射的网页操作。`api call` 使用兼容的 `service.Action` 名称与 JSON 对象参数，但网页后端仅转换确证的网页路径、字段，并非声称同名 /v3 签名接口可用；未支持操作明确报错。网页 zoneCode 通过规格目录映射到 zoneId/regionId/clusterId，绝不直接重命名。网页镜像目录的分页和返回形状与 OpenAPI 不同；停止服务默认保留环境，实际破坏性行为须自行核对。

网页请求的公开前端依据：[ack](https://ai.paratera.com/assets/js/ack-D4I28UNm.js)、[containerList](https://ai.paratera.com/assets/js/containerList-C36_4OnN.js)、[add](https://ai.paratera.com/assets/js/add-CWKrfx1I.js)。目前只有目录、镜像分页、库存、询价与响应信封经只读实测；写操作与非空实例返回归一仍待真实验证。网页后端不支持 `power reboot`。网页 `images` 输出的 `imageUuid` 已归一成创建接口所需 ID：优先公共镜像 `imageId`，否则使用 `providerImageUuid`；源数据中的 `imageUuid`（可能是 `ackci-...`）另存为 `sourceImageUuid`。`create --image` 请直接使用 `--backend web images` 所返回的 `imageUuid`，不要使用 `sourceImageUuid`。目录 `listPrice` 为 0 **不代表免费**：询价实际可返回非零价格；创建前请先 `quote` 核对返回价。

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
