# Paratera 实际接口核验记录

> 2026-09-23 的只读调查及端到端记录。公开 API 文档与当前网页登录态是两套协议；“已实测”不代表所有账户和未来版本均适用。不得在文档或日志保存凭据、网页登录态、SSH 密码或实例标识。

## OpenAPI 文档：已封装，不等于线上可用

- [API 3.0 简介](https://ai.paratera.com/document/openapi)与[请求结构](https://ai.paratera.com/document/openapi/request/HttpRequestMethod)记载 `https://ai.blsc.cn` 和 `POST /v3/{service}/{Action}`；[签名示例](https://ai.paratera.com/document/openapi/request/SignMethod)用 X-TC-*，而[公共参数](https://ai.paratera.com/document/openapi/request/HttpRequestParameters)规定 X-AIC-*，两处冲突。不能凭旧文档推定当前入口或头格式可用。
- 2026-09-23 实测：在 `https://ai.blsc.cn/v3/region/DescribeZones` 和 `https://ai.paratera.com/v3/region/DescribeZones`，POST 均返回 **HTTP 405 HTML**，GET 均落到 SPA HTML，而非 API 的 JSON 业务响应。405 是当前 Web 路由现象，不能诊断为签名失败。
- [公开操作清单及来源](api-coverage.md)共 **46 个**（容器 25、云服务器/云盘/规格/可用区 21）；这些是按文档封装，**不是 46 个现网可用的证明**。官方公开文档没有 EIP CRUD 定义，不虚构接口。[容器公共镜像页](https://ai.paratera.com/document/openapi/container/production/DescribeACKPublicImages)所列 service/action 与示例 path 不一致；[计算任务页](https://ai.paratera.com/document/openapi/computer/jobs/DescribeJobs)未注明签名 service。

## 当前网页登录态 backend：公开代码与已实测

- [当前前端请求构造器](https://ai.paratera.com/assets/js/index-UohjpHf1.js)使用 `/platform` 基址、网页 token 与 `Ai-Authorization`，**不是** AccessKey HMAC 签名协议。2026-09-23，父代理在本人登录浏览器中以只读请求确认网页登录态返回业务 `code=200`；`/platform/resourceUserConfig/getList` 返回配额对象，**不是实例列表**。会话有效期限及关闭浏览器后是否失效尚不明确，不承诺免登录复用。
- 当前操作定义见 [ack 模块](https://ai.paratera.com/assets/js/ack-D4I28UNm.js)、[实例页面](https://ai.paratera.com/assets/js/containerList-C36_4OnN.js)、[创建页面](https://ai.paratera.com/assets/js/add-CWKrfx1I.js)：列表 `POST /platform/ack/service/getUserResourceContainerServiceDetailList`；库存 `POST /platform/resourceContainerService/describeServiceAvailableResource`；询价 `POST /platform/resourcePrice/checkContainerPrice`；任务 `POST /platform/jobs/ack`；SSH、Jupyter、TensorBoard 是 `POST /platform/ack/service/describe/{ssh,jupyter,tensorboard}`。网页请求用 `zoneId/regionId/clusterId`，不能照搬旧 OpenAPI 的 `zoneCode`。
- CLI 网页模式**已实测通过**：`zones` 返回 6、`types` 返回 33、指定 zone 库存返回 7、`images` 返回 3、`list` 返回 0；公共镜像总数 **50**，已经分页核验。规格 `vgpu.split10.rtx5090.xeon6530.xlarge` 位于 `cn-zhongwei-ec`：目录标价 **0**，实际询价 **0.3 元/小时**；不能把目录标价当成最终价格。
- **镜像 ID 差异**：[创建页面脚本](https://ai.paratera.com/assets/js/add-CWKrfx1I.js)的下拉值 `o.imageId || o.providerImageUuid` 写入 `param.imageUuid`，创建时原样提交。公共镜像即使原始 `imageType=="custom"` 也被页面归入 public，默认选取 `imageId`；同一行的 `imageUuid` 可以是另一种 `ackci-…` 标识。因此**网页登录态创建：公共镜像 `row.imageId -> payload.imageUuid`；自定义/共享镜像取 `providerImageUuid`**。不推定旧文档 OpenAPI 采用相同语义。
- 父代理经授权已创建**一个**测试实例：创建任务 `done=true, success=true`，状态 `Running`；`ssh --json` 和 `endpoints` 成功，验证过程无密码泄漏。`stop` 默认 `saveEnv=true`，任务成功并观察到 `Stopped`。[旧关机文档](https://ai.paratera.com/document/openapi/container/services/StopServices)称 `saveEnv=false` 将删除文件和配置；[当前实例页面](https://ai.paratera.com/assets/js/containerList-C36_4OnN.js)在确认关机时明确设置 `saveEnv=true`。关机并非删除，保存环境的长期费用尚未核实。
- **待验证，绝不可记为完成**：该实例再次开机、最终删除，以及新版默认 `wait` 行为。删除须看任务和实例列表状态，不能仅凭请求成功断定资源已释放。
- 当前 web backend 未实现 reboot；但[公开 ack 模块](https://ai.paratera.com/assets/js/ack-D4I28UNm.js)的动态映射为 `endpoint: "ack/service/" + operation`、`method: "POST"`；[实例页面](https://ai.paratera.com/assets/js/containerList-C36_4OnN.js)确认重启后调用 `operationDC("reboot", s, …)`，`s` 来自 `{zoneId: item.zoneId, serviceUuids: [item.serviceUuid]}`。因此代码支持候选 **`POST /platform/ack/service/reboot`** 和上述请求体；仍未实测，也不应宣称 CLI 已支持。
- [API Access Key 管理页脚本](https://ai.paratera.com/assets/js/index-pJfl8glW.js)仍只链接[SDK 中心](https://ai.paratera.com/document/computer/api/sdk)和[旧 OpenAPI 文档](https://ai.paratera.com/document/openapi)，未公布新的可用签名 ingress。

## 待办与保密边界

1. 完成开机、删除和 `wait` 的低成本验证后，据实更新本记录；失败同样记录。
2. 浏览器 session/token、AccessKey、实例标识、SSH 密码不进入仓库、提交、日志或共享报告。SSH 原始 JSON 可能包含明文密码，展示时必须脱敏。
3. 官方若提供可工作的签名 ingress，需分别核验 host、path、头、service 与业务码；网页 token 不能代替 OpenAPI 密钥。
