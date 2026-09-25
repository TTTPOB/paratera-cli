# Paratera CLI

项目仓库：[TTTPOB/paratera-cli](https://github.com/TTTPOB/paratera-cli)。

基于 **httpx2 异步客户端**的个人容器 CLI。当前默认 `--backend web`：复用**本人已登录**的网页登录态；不收集用户名、密码或验证码，也不采用过时的官方 Python SDK。文档 OpenAPI 的签名协议另行封装，当前生产 `/v3` 入口不可达，不能当作可用替代方案。

`uv sync` 后用 `uv run paratera --help` 查看命令。CLI 默认使用 `web`；如确需文档 OpenAPI，可在子命令前显式指定全局选项 `--backend openapi`，但生产 `/v3` 入口目前不可用。

## 导入本人网页登录态

续期的关键是**服务端 cookie**，不是 token：token 是 1 小时的 JWT，cookie 有效时 CLI 会用 `/platform/tsinghua/authAndGetCoupon` 自动换取新 token 并写回文件。

浏览器登录后，在 `https://ai.paratera.com` 的开发者工具控制台执行：

```js
copy(JSON.stringify({ cookies: document.cookie, accessCode: localStorage.getItem('accessCode') }))
```

再经**标准输入**导入（**不要放在命令行参数或 Git**）：

```bash
pbpaste | uv run paratera session import   # 无 pbpaste 用 xclip -selection clipboard -o
uv run paratera session refresh            # 可选：立即校验 cookie 是否还能换 token
uv run paratera zones
```

默认保存于 `~/.local/share/creds/paratera-session.json`，文件权限 **0600**；`--session-file PATH` 可更改路径。导入 JSON 可含 `token`、`cookies`、`accessCode`：给出后两者才具备续期能力，只给 `token`（或使用 `PARATERA_TOKEN` 环境变量，其优先于文件且不读写文件）时按旧行为一次性使用。

cookie 会话与清华统一认证 SSO 的真实期限均未验证；SSO 失效后无密码账号仍须在浏览器重新登录并再次导入。`session refresh` 报错即表示 cookie 已失效。网页登录态请求包含 `token` 及 `Ai-Authorization: Bearer …`，**不是** AccessKey HMAC 签名。请避免打印原始请求头、cookie、SSH 密码和带认证信息的 URL。

## 常用流程

1. 查配置与价格：先列规格、库存与镜像，再以询价响应确认费用；目录标价可能是零，但实际询价非零。
2. 创建：提供已查得的可用区、规格和 `images` 显示的创建用镜像 ID。创建后默认等待任务结束；如仅提交请显式 `--no-wait`。
3. 使用与回收：`get` 查实例，`ssh` 仅打印连接命令，`power off` 默认保留环境；不再需要时先核对目标，再用 `delete --yes`。

```bash
uv run paratera zones
uv run paratera types --zone cn-zhongwei-ec
uv run paratera availability --zone cn-zhongwei-ec
uv run paratera images --zone cn-zhongwei-ec
uv run paratera quote --zone ZONE --name NAME --model MODEL --image IMAGE_ID
uv run paratera create --zone ZONE --name NAME --model MODEL --image IMAGE_ID
uv run paratera get --json
uv run paratera get --id INSTANCE_UUID
uv run paratera ssh --id INSTANCE_UUID
uv run paratera endpoints --id INSTANCE_UUID
uv run paratera power off --id INSTANCE_UUID
uv run paratera power on --id INSTANCE_UUID
uv run paratera power reboot --id INSTANCE_UUID
uv run paratera jobs get JOB_UUID
uv run paratera delete --id INSTANCE_UUID --yes
```

**实测范围**（详见[验证记录](docs/live-validation.md)）：网页登录态 zones、types、availability、images（含分页）、get、quote、create、SSH/附加端点**查询**以及默认等待的 power on/reboot/off/delete 均通过；删除后活动和 Recycled 列表均未找到该测试实例。已另行通过实际 SSH 登录执行只读识别命令并成功退出；文件持久性和实际扣款仍未验证。网页模式仅映射常用子集，不等同于公开文档全部 46 个操作可用。

`get` 支持 `--page/--page-size`、`--id/--zone`。需要唯一实例时可省略 `--id`，但账户中必须恰好只有一个匹配项。`ssh` 仅打印 `ssh -p PORT USER@HOST`，**不会自动连接**；`ssh --json`、`endpoints` 默认脱敏密码及 URL 中 token，仅显式 `--show-secrets` 才显示，请勿粘贴其原始输出。创建默认按需 `--billing-type PostPaid --count 1`，包月使用 `--billing-type PrePaid --pay-period MONTHS`；创建会冻结或扣费，不强制交互，请先询价。网页 `images` 的 `imageUuid` 是已归一的创建用 ID：公共镜像取原始 `imageId`，而原始 `ackci-…` 在 `sourceImageUuid`，不要将后者当网页创建参数。

`power off` 默认 `saveEnv=true`，仅显式 `--discard-env` 才不保存文件与环境；按需默认 `STOP_CHARGING`、包月默认 `KEEP_CHARGING`，冲突模式会报错。`delete` 必须指定 `--yes`。创建、开关机、重启、删除是异步任务：CLI 默认**等待完成**，超时默认 **600 秒**、每 **5 秒**轮询；可用 `--timeout SECONDS --poll-interval SECONDS` 调整，`--no-wait` 只提交并输出 job ID（不代表完成）。`jobs get JOB_UUID` 查询一次，`jobs wait JOB_UUID` 持续等待；任务失败或超时返回非零，超时后服务端操作仍可能继续。下次查询请继续使用**相同 backend**：默认网页模式为 `uv run paratera jobs get JOB_UUID`；若原命令显式指定 `--backend openapi`，查询时也要指定。

## Python 异步用法

网页登录态 backend 不需要 AccessKey 文件；下面只读取你已导入的 session，**不要打印原始 SSH/Jupyter 响应或凭据**：

```python
import asyncio

from paratera_cli.tasks import wait_for_jobs
from paratera_cli.web import WebClient, WebParateraAPI

async def main():
    async with WebClient() as client:
        api = WebParateraAPI(client)
        instances = await api.call("ackcs.DescribeServices", {"pageNum": 1, "pageSize": 100})
        print(len(instances["rows"]))
        # When submitting a lifecycle operation, pass its returned jobs:
        # completed = await wait_for_jobs(api, jobs, batch=True)

asyncio.run(main())
```

`wait_for_jobs(api, jobs, timeout=600, poll_interval=5, batch=True)` 是可选等待；底层 `api.call` 只提交操作并返回任务，不隐式轮询。网页 `api call` 只支持已有映射，可用 `uv run paratera api list` 查看，调用如 `uv run paratera api call ack_job.DescribeJobs --params '{"jobUuid":"JOB_UUID"}' --json`。`api call` 不自动执行生命周期等待。

## 历史文档 OpenAPI 封装

`ParateraClient` + `ParateraAPI` 根据[公开文档覆盖清单](docs/api-coverage.md)提供 **46 个具名异步操作**（容器 25，其他 21），但两个官网域名的 `POST /v3/region/DescribeZones` 均已实测返回 HTTP 405 HTML，因此目前仅是历史文档协议的封装，**不是已验证生产可用的 SDK**。网页登录态与签名协议不可混用；公开文档未给出 EIP CRUD，不虚构。若取得新版 OpenAPI ingress，需独立核验签名头、service、path 和业务码。

开发与检查见[开发文档](docs/development.md)；逐项差异及真实结果见[验证记录](docs/live-validation.md)。
