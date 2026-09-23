"""Discoverable, thin asynchronous wrappers for the published Paratera OpenAPI."""

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .client import ParateraClient


@dataclass(frozen=True)
class Operation:
    service: str
    action: str
    path: str
    documentation: str


_DOC = "https://ai.paratera.com/document/openapi/"
# Each entry is a published documentation group, signing service and explicit action set.
# Paths were checked against the individual documentation pages; exceptions follow below.
_GROUPS = (
    ("ack_product", "container/production", "DescribeACKServiceTypes DescribeACKVolumeTypes DescribeACKAvailableResources DescribeACKPublicImages"),
    ("ackcs", "container/services", "InquiryPriceCreateServices CreateServices DescribeServices StopServices StartServices RebootServices DeleteServices ChangeServicesModel ChangeServicesBillingType ChangeServicesExpireStrategy RenewServices DescribeServicesSSH DescribeServicesJupyter DescribeServicesTensorBoard"),
    ("ackci", "container/images", "CreateImages DeleteImages DescribeImages"),
    ("ackcv", "container/volumes", "DescribeVolumes ResizeVolumes DeleteVolumes"),
    ("ack_job", "container/jobs", "DescribeJobs"),
    ("region", "region", "DescribeZones"),
    ("product", "computer/production", "DescribeInstanceTypes DescribeDiskTypes DescribeImages DescribeNetworkTypes"),
    ("ecs", "computer/instance", "DescribeInstances RunInstances InquirePriceRunInstances StartInstances StopInstances RebootInstances DeleteInstances RenewInstances"),
    ("ebs", "computer/disk", "DescribeDisks CreateDisks InquirePriceCreateDisks AttachDisks DetachDisks DeleteDisks RenewDisks"),
    ("job", "computer/jobs", "DescribeJobs"),
)

OPERATIONS: dict[str, Operation] = {
    f"{service}.{action}": Operation(
        service=service,
        action=action,
        path=f"/v3/{service}/{action}",
        documentation=_DOC + group + "/" + (
            "InquiryPriceRunInstances" if service == "ecs" and action == "InquirePriceRunInstances" else action
        ),
    )
    for service, group, actions in _GROUPS
    for action in actions.split()
}
# The public-images page claims service/action ack_product.DescribeACKPublicImages
# but prints /v3/product/DescribeImages. This candidate is NOT live-verified.
# Never silently fall back to the conflicting route.


def _payload(params: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return {**(params or {}), **kwargs}


def _specified(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


class ParateraAPI:
    """Wrap the client without reshaping response data or handling credentials."""

    def __init__(self, client: ParateraClient) -> None:
        self.client = client

    async def call(self, operation: str, params: Mapping[str, Any] | None = None) -> Any:
        """Call an operation such as ``ackcs.StopServices``; return raw data.

        A stopped container retains its environment unless saveEnv is explicitly
        false. This applies equally to this low-level entry point.
        """
        spec = OPERATIONS[operation]
        body = dict(params or {})
        if operation == "ackcs.StopServices":
            body.setdefault("saveEnv", True)
            body.setdefault("stoppedMode", "STOP_CHARGING")
        return await self.client.request(spec.service, spec.action, body, path=spec.path)

    async def describe_ack_service_types(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("ack_product.DescribeACKServiceTypes", _payload(params, **kwargs))

    async def describe_ack_volume_types(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("ack_product.DescribeACKVolumeTypes", _payload(params, **kwargs))

    async def describe_ack_available_resources(self, service_models: Sequence[Mapping[str, Any]]) -> Any:
        return await self.call("ack_product.DescribeACKAvailableResources", {"serviceModels": list(service_models)})

    async def describe_ack_public_images(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("ack_product.DescribeACKPublicImages", _payload(params, **kwargs))

    async def inquiry_price_create_services(
        self, zone_code: str, alias_name: str, service_model: str, image_uuid: str, billing_type: str,
        *, count: int | None = None, pay_period: int | None = None,
        auto_continue: bool | None = None, **kwargs: Any,
    ) -> Any:
        body = _specified(zoneCode=zone_code, aliasName=alias_name, serviceModel=service_model,
                          imageUuid=image_uuid, billingType=billing_type, count=count,
                          payPeriod=pay_period, autoContinue=auto_continue)
        return await self.call("ackcs.InquiryPriceCreateServices", _payload(body, **kwargs))

    async def create_services(
        self, zone_code: str, alias_name: str, service_model: str, image_uuid: str, billing_type: str,
        *, count: int | None = None, pay_period: int | None = None,
        auto_continue: bool | None = None, **kwargs: Any,
    ) -> Any:
        body = _specified(zoneCode=zone_code, aliasName=alias_name, serviceModel=service_model,
                          imageUuid=image_uuid, billingType=billing_type, count=count,
                          payPeriod=pay_period, autoContinue=auto_continue)
        return await self.call("ackcs.CreateServices", _payload(body, **kwargs))

    async def describe_services(
        self, *, service_uuid: str | None = None, alias_name: str | None = None,
        service_status: str | None = None, service_type: str | None = None,
        page_num: int | None = None, page_size: int | None = None,
        zone_code: str | None = None, **kwargs: Any,
    ) -> Any:
        body = _specified(serviceUuid=service_uuid, aliasName=alias_name,
                          serviceStatus=service_status, serviceType=service_type,
                          pageNum=page_num, pageSize=page_size, zoneCode=zone_code)
        return await self.call("ackcs.DescribeServices", _payload(body, **kwargs))

    async def iter_services(
        self, *, page_size: int = 100, max_pages: int = 100, **filters: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield rows from the published paginated data shape, with a finite bound."""
        if page_size < 1 or max_pages < 1:
            raise ValueError("page_size and max_pages must be positive")
        previous: list[dict[str, Any]] | None = None
        for page in range(1, max_pages + 1):
            data = await self.describe_services(page_num=page, page_size=page_size, **filters)
            rows = data["rows"]
            if not rows or rows == previous:
                break
            for row in rows:
                yield row
            total = data.get("total")
            if (isinstance(total, int) and page * page_size >= total) or len(rows) < page_size:
                break
            previous = rows

    async def stop_services(
        self, zone_code: str, service_uuid: str, *, save_env: bool = True,
        stopped_mode: str = "STOP_CHARGING",
    ) -> Any:
        return await self.call("ackcs.StopServices", {"zoneCode": zone_code,
            "serviceUuids": [service_uuid], "saveEnv": save_env, "stoppedMode": stopped_mode})

    async def start_services(self, zone_code: str, service_uuid: str) -> Any:
        return await self.call("ackcs.StartServices", {"zoneCode": zone_code, "serviceUuids": [service_uuid]})

    async def reboot_services(self, zone_code: str, service_uuid: str) -> Any:
        return await self.call("ackcs.RebootServices", {"zoneCode": zone_code, "serviceUuids": [service_uuid]})

    async def delete_services(self, zone_code: str, service_uuid: str) -> Any:
        return await self.call("ackcs.DeleteServices", {"zoneCode": zone_code, "serviceUuids": [service_uuid]})

    async def change_services_model(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("ackcs.ChangeServicesModel", _payload(params, **kwargs))

    async def change_services_billing_type(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("ackcs.ChangeServicesBillingType", _payload(params, **kwargs))

    async def change_services_expire_strategy(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("ackcs.ChangeServicesExpireStrategy", _payload(params, **kwargs))

    async def renew_services(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("ackcs.RenewServices", _payload(params, **kwargs))

    async def describe_services_ssh(self, zone_code: str, service_uuid: str) -> Any:
        return await self.call("ackcs.DescribeServicesSSH", {"zoneCode": zone_code, "serviceUuids": [service_uuid]})

    async def describe_services_jupyter(self, zone_code: str, service_uuid: str) -> Any:
        return await self.call("ackcs.DescribeServicesJupyter", {"zoneCode": zone_code, "serviceUuids": [service_uuid]})

    async def describe_services_tensor_board(self, zone_code: str, service_uuid: str) -> Any:
        return await self.call("ackcs.DescribeServicesTensorBoard", {"zoneCode": zone_code, "serviceUuids": [service_uuid]})

    async def create_images(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("ackci.CreateImages", _payload(params, **kwargs))

    async def delete_images(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("ackci.DeleteImages", _payload(params, **kwargs))

    async def describe_images(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("ackci.DescribeImages", _payload(params, **kwargs))

    async def describe_volumes(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("ackcv.DescribeVolumes", _payload(params, **kwargs))

    async def resize_volumes(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("ackcv.ResizeVolumes", _payload(params, **kwargs))

    async def delete_volumes(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("ackcv.DeleteVolumes", _payload(params, **kwargs))

    async def describe_jobs(self, job_uuid: str) -> Any:
        return await self.call("ack_job.DescribeJobs", {"jobUuid": job_uuid})

    async def describe_zones(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("region.DescribeZones", _payload(params, **kwargs))

    async def describe_instance_types(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("product.DescribeInstanceTypes", _payload(params, **kwargs))

    async def describe_disk_types(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("product.DescribeDiskTypes", _payload(params, **kwargs))

    async def describe_compute_images(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("product.DescribeImages", _payload(params, **kwargs))

    async def describe_network_types(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("product.DescribeNetworkTypes", _payload(params, **kwargs))

    async def describe_instances(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("ecs.DescribeInstances", _payload(params, **kwargs))

    async def run_instances(
        self, zone_code: str, alias_name: str, ecs_model: str, image_uuid: str,
        root_disk_type: str, root_disk_size: int, billing_type: str, network_size: int,
        network_type: str, disk_type: str, disk_size: int, **kwargs: Any,
    ) -> Any:
        body = dict(zoneCode=zone_code, aliasName=alias_name, ecsModel=ecs_model,
                    imageUuid=image_uuid, rootDiskType=root_disk_type, rootDiskSize=root_disk_size,
                    billingType=billing_type, networkSize=network_size, networkType=network_type,
                    diskType=disk_type, diskSize=disk_size)
        return await self.call("ecs.RunInstances", _payload(body, **kwargs))

    async def inquire_price_run_instances(
        self, zone_code: str, alias_name: str, ecs_model: str, image_uuid: str,
        root_disk_type: str, root_disk_size: int, billing_type: str, network_size: int,
        network_type: str, disk_type: str, disk_size: int, **kwargs: Any,
    ) -> Any:
        body = dict(zoneCode=zone_code, aliasName=alias_name, ecsModel=ecs_model,
                    imageUuid=image_uuid, rootDiskType=root_disk_type, rootDiskSize=root_disk_size,
                    billingType=billing_type, networkSize=network_size, networkType=network_type,
                    diskType=disk_type, diskSize=disk_size)
        return await self.call("ecs.InquirePriceRunInstances", _payload(body, **kwargs))

    async def start_instances(self, zone_code: str, ecs_uuid: str) -> Any:
        return await self.call("ecs.StartInstances", {"zoneCode": zone_code, "ecsUuids": [ecs_uuid]})

    async def stop_instances(self, zone_code: str, ecs_uuid: str) -> Any:
        return await self.call("ecs.StopInstances", {"zoneCode": zone_code, "ecsUuids": [ecs_uuid]})

    async def reboot_instances(self, zone_code: str, ecs_uuid: str) -> Any:
        return await self.call("ecs.RebootInstances", {"zoneCode": zone_code, "ecsUuids": [ecs_uuid]})

    async def delete_instances(self, zone_code: str, ecs_uuid: str) -> Any:
        return await self.call("ecs.DeleteInstances", {"zoneCode": zone_code, "ecsUuids": [ecs_uuid]})

    async def renew_instances(self, zone_code: str, ecs_uuid: str, period: int) -> Any:
        return await self.call("ecs.RenewInstances", {"zoneCode": zone_code, "ecsUuids": [ecs_uuid], "period": period})

    async def describe_disks(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.call("ebs.DescribeDisks", _payload(params, **kwargs))

    async def create_disks(
        self, zone_code: str, alias_name: str, disk_type: str, disk_size: int,
        billing_type: str, **kwargs: Any,
    ) -> Any:
        body = dict(zoneCode=zone_code, aliasName=alias_name, diskType=disk_type,
                    diskSize=disk_size, billingType=billing_type)
        return await self.call("ebs.CreateDisks", _payload(body, **kwargs))

    async def inquire_price_create_disks(
        self, zone_code: str, alias_name: str, disk_type: str, disk_size: int,
        billing_type: str, **kwargs: Any,
    ) -> Any:
        body = dict(zoneCode=zone_code, aliasName=alias_name, diskType=disk_type,
                    diskSize=disk_size, billingType=billing_type)
        return await self.call("ebs.InquirePriceCreateDisks", _payload(body, **kwargs))

    async def attach_disks(self, zone_code: str, ecs_uuid: str, ebs_uuid: str) -> Any:
        return await self.call("ebs.AttachDisks", {"zoneCode": zone_code, "ecsUuid": ecs_uuid, "ebsUuids": [ebs_uuid]})

    async def detach_disks(self, zone_code: str, ecs_uuid: str, ebs_uuid: str) -> Any:
        return await self.call("ebs.DetachDisks", {"zoneCode": zone_code, "ecsUuid": ecs_uuid, "ebsUuids": [ebs_uuid]})

    async def delete_disks(self, zone_code: str, ebs_uuid: str) -> Any:
        return await self.call("ebs.DeleteDisks", {"zoneCode": zone_code, "ebsUuids": [ebs_uuid]})

    async def renew_disks(self, zone_code: str, ebs_uuid: str, period: int) -> Any:
        return await self.call("ebs.RenewDisks", {"zoneCode": zone_code, "ebsUuids": [ebs_uuid], "period": period})

    async def describe_compute_jobs(self, job_uuid: str) -> Any:
        return await self.call("job.DescribeJobs", {"jobUuid": job_uuid})
