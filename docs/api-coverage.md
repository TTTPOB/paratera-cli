# Paratera OpenAPI 覆盖清单

本模块提供 **46 个公开操作**（容器 25、云服务器/云盘 21）：`ParateraAPI(client)` 的具名异步方法，以及 `await api.call("service.Action", params)`。所有方法原样返回 `client.request(..., path=...)` 的 `data`，不包装异步任务结果、不读取或打印凭据。静态 `OPERATIONS` 记录每项的 service、action、path、documentation；可直接供 CLI 查询和调用。

各行 Action 对应其官方文档目录中的同名页面，特例写在后文。下列路径逐页核对了官方文档中的请求路径（不是依据 service 名猜测），**没有线上认证请求验证**：

| 类别 | 签名 service | 文档目录 | Action（请求路径为 `/v3/{service}/{Action}`） |
| --- | --- | --- | --- |
| 容器产品 | `ack_product` | [container/production](https://ai.paratera.com/document/openapi/container/production) | DescribeACKServiceTypes、DescribeACKVolumeTypes、DescribeACKAvailableResources、DescribeACKPublicImages¹ |
| 容器实例 | `ackcs` | [container/services](https://ai.paratera.com/document/openapi/container/services/InquiryPriceCreateServices) | InquiryPriceCreateServices、CreateServices、DescribeServices、StopServices、StartServices、RebootServices、DeleteServices、ChangeServicesModel、ChangeServicesBillingType、ChangeServicesExpireStrategy、RenewServices、DescribeServicesSSH、DescribeServicesJupyter、DescribeServicesTensorBoard |
| 私有镜像 | `ackci` | [container/images](https://ai.paratera.com/document/openapi/container/images/DescribeImages) | CreateImages、DeleteImages、DescribeImages |
| 容器卷 | `ackcv` | [container/volumes](https://ai.paratera.com/document/openapi/container/volumes/DescribeVolumes) | DescribeVolumes、ResizeVolumes、DeleteVolumes |
| 容器任务 | `ack_job` | [container/jobs/DescribeJobs](https://ai.paratera.com/document/openapi/container/jobs/DescribeJobs) | DescribeJobs |
| 可用区 | `region` | [region/DescribeZones](https://ai.paratera.com/document/openapi/region/DescribeZones) | DescribeZones |
| 云产品 | `product` | [computer/production](https://ai.paratera.com/document/openapi/computer/production/DescribeImages) | DescribeInstanceTypes、DescribeDiskTypes、DescribeImages、DescribeNetworkTypes |
| 云服务器 | `ecs` | [computer/instance](https://ai.paratera.com/document/openapi/computer/instance/RunInstances) | DescribeInstances、RunInstances、InquirePriceRunInstances²、StartInstances、StopInstances、RebootInstances、DeleteInstances、RenewInstances |
| 云盘 | `ebs` | [computer/disk](https://ai.paratera.com/document/openapi/computer/disk/DescribeDisks) | DescribeDisks、CreateDisks、InquirePriceCreateDisks、AttachDisks、DetachDisks、DeleteDisks、RenewDisks |
| 云服务器任务 | `job` | [computer/jobs/DescribeJobs](https://ai.paratera.com/document/openapi/computer/jobs/DescribeJobs) | DescribeJobs³ |

¹ [公共镜像页](https://ai.paratera.com/document/openapi/container/production/DescribeACKPublicImages)写明签名 service=`ack_product`、action=`DescribeACKPublicImages`，但展示请求路径 `/v3/product/DescribeImages`，与云产品镜像接口冲突。注册表**仅使用未实测候选** `/v3/ack_product/DescribeACKPublicImages`；不自动回退到冲突路径。需要人工核实官方最终路由，必要时可直接使用 `client.request(..., path=...)`。

² [询价文档页面](https://ai.paratera.com/document/openapi/computer/instance/InquiryPriceRunInstances)文件名为 `InquiryPriceRunInstances`，实际 action/path 是 `InquirePriceRunInstances`；注册表的 documentation 保留页面原名。

³ [计算任务页](https://ai.paratera.com/document/openapi/computer/jobs/DescribeJobs)给出 `/v3/job/DescribeJobs`，未明确注明签名 service，当前以 `job` 为**未验证候选**，需实测确认。

## 使用约定

```python
api = ParateraAPI(client)
rows = await api.describe_services(zone_code="cn-example", page_num=1, page_size=100)
async for service in api.iter_services(zone_code="cn-example", page_size=100, max_pages=100):
    ...
await api.stop_services("cn-example", "container-uuid")
await api.call("ackcs.ChangeServicesModel", {"zoneCode": "cn-example", "serviceUuids": ["container-uuid"]})
```

- 容器询价/创建显式接收 `zone_code, alias_name, service_model, image_uuid, billing_type`，其余可通过 `count/pay_period/auto_continue` 或原始 `**kwargs` 传入。查询库存传 `service_models=[{"zoneCode": ..., "serviceModel": ...}]`。
- `describe_services` 返回官方 `data` 分页对象（含 `rows`），`iter_services` 逐行遍历；默认最多 100 页，空页、短页、重复页或已达 `total` 即停止，可显式增大 `max_pages`。不是实时一致性保证。
- 单容器生命周期和 SSH/Jupyter/TensorBoard 方法仅接受**一个** `service_uuid`，提交 `serviceUuids: [service_uuid]`；当前官方接口未支持批量。读取 SSH/Jupyter/TensorBoard 的原始 `data` 可能含密码或 token，调用方勿记录日志。
- `stop_services` 默认 `saveEnv=True`、`stoppedMode=STOP_CHARGING`；`call("ackcs.StopServices", ...)` 同样默认保留环境，明确传 `saveEnv=False` 则保持原值。官方警告不保存环境会删除文件/配置，包月计费仅支持 `KEEP_CHARGING`，调用方自行选择。其他可能有破坏性的操作不自动执行。
- 两种 `DescribeJobs` 分别为 `describe_jobs`（`ack_job`）和 `describe_compute_jobs`（`job`），仅查询一次并原样返回任务列表；`done=False, success=False` 意味执行中，不提供隐式轮询。两种 `DescribeImages` 分别为 `describe_images`（容器私有镜像）和 `describe_compute_images`（云产品镜像）。
- 云服务器创建/询价要求 `zone_code, alias_name, ecs_model, image_uuid, root_disk_type, root_disk_size, billing_type, network_size, network_type, disk_type, disk_size`；云盘创建/询价要求 `zone_code, alias_name, disk_type, disk_size, billing_type`。云盘挂载、卸载和删除使用文档示例中的 `ebsUuids` 复数数组；[挂载页](https://ai.paratera.com/document/openapi/computer/disk/AttachDisks)参数表却标为 `ebsUuid`，**字段拼写待线上验证**。没有公开 EIP 操作，不虚构。
- 低频方法接受 `params` dict 加原始 `**kwargs`，后者覆盖同名 dict 字段；`call` 只允许注册表内的操作，路由由注册表提供，不能任意构造远端路径。

**验证范围：** `tests/test_api.py` 使用离线模拟客户端测试路由、有效载荷、默认保留环境及分页；不调用真实 API，也不保证线上 ingress 可用、签名服务或业务参数经实际认证验证。需在能访问的环境由用户明确授权后逐项核实候选路由和存在文档冲突的字段。
