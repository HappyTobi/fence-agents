import logging
import re
import time

from fencing import fail_usage

logger = logging.getLogger(__name__)

FENCE_SUBNET_NAME = "fence-subnet"
FENCE_INBOUND_RULE_NAME = "FENCE_DENY_ALL_INBOUND"
FENCE_INBOUND_RULE_DIRECTION = "Inbound"
FENCE_OUTBOUND_RULE_NAME = "FENCE_DENY_ALL_OUTBOUND"
FENCE_OUTBOUND_RULE_DIRECTION = "Outbound"
FENCE_STATE_OFF = "off"
FENCE_STATE_ON = "on"
FENCE_TAG_SUBNET_ID = "FENCE_TAG_SUBNET_ID"
FENCE_TAG_IP_TYPE = "FENCE_TAG_IP_TYPE"
FENCE_TAG_IP = "FENCE_TAG_IP"
IP_TYPE_DYNAMIC = "Dynamic"
MAX_RETRY = 10
RETRY_WAIT = 5
NETWORK_MGMT_CLIENT_API_VERSION = "2021-05-01"
AZURE_RHEL8_COMPUTE_VERSION = "27.2.0"
AZURE_COMPUTE_VERSION_5 = "5.0.0"

# Azure Instance Metadata Service (IMDS) endpoint and API version
AZURE_IMDS_ENDPOINT = "http://169.254.169.254/metadata/instance"
AZURE_IMDS_API_VERSION = "2017-08-01"

# Azure cloud environment constants
AZURE_CLOUD_PUBLIC = "public"
AZURE_CLOUD_CHINA = "china"
AZURE_CLOUD_US_GOV = "usgov"
AZURE_CLOUD_GERMANY = "germany"
AZURE_CLOUD_STACK = "stack"
AZURE_CLOUD_NAME_PUBLIC = "AZURE_PUBLIC_CLOUD"
AZURE_CLOUD_NAME_CHINA = "AZURE_CHINA_CLOUD"
AZURE_CLOUD_NAME_US_GOV = "AZURE_US_GOV_CLOUD"

class AzureSubResource:
    Type = None
    Name = None

class AzureResource:
    Id = None
    SubscriptionId = None
    ResourceGroupName = None
    ResourceName = None

    def __init__(self):
        self.SubResources = []

class AzureConfiguration:
    RGName = None
    VMName = None
    SubscriptionId = None
    Cloud = None
    UseMSI = None
    Tenantid = None
    ApplicationId = None
    ApplicationKey = None
    Verbose = None

def get_from_metadata(parameter):
    import requests
    started = time.monotonic()
    logger.debug("operation=imds parameter=%s api_version=%s outcome=request_started", parameter, AZURE_IMDS_API_VERSION)
    try:
        r = requests.get(AZURE_IMDS_ENDPOINT, params={"api-version": AZURE_IMDS_API_VERSION}, headers = {"Metadata":"true"})
        logger.debug("operation=imds parameter=%s http_status=%s outcome=response_received elapsed_ms=%s", parameter, r.status_code, int((time.monotonic() - started) * 1000))
        value = str(r.json()["compute"][parameter])
        logger.debug("operation=imds parameter=%s outcome=value_read elapsed_ms=%s", parameter, int((time.monotonic() - started) * 1000))
        return value
    except (requests.RequestException, ValueError, KeyError, TypeError) as error:
        logger.warning("Not able to use metadata service. Am I running in Azure? operation=imds parameter=%s error_type=%s elapsed_ms=%s", parameter, type(error).__name__, int((time.monotonic() - started) * 1000))

    return None

def get_azure_resource(id):
    match = re.match(r'(/subscriptions/([^/]*)/resourceGroups/([^/]*))(/providers/([^/]*/[^/]*)/([^/]*))?((/([^/]*)/([^/]*))*)', id)
    if not match:
        fail_usage(f"{{get_azure_resource}} cannot parse resource id {id}")

    logger.debug("{get_azure_resource} found %s matches for %s", len(match.groups()), id)
    iGroup = 0
    while iGroup < len(match.groups()):
        logger.debug("{get_azure_resource} group %s: %s", iGroup, match.group(iGroup))
        iGroup += 1

    resource = AzureResource()
    resource.Id = id
    resource.SubscriptionId = match.group(2)

    if len(match.groups()) > 3:
        resource.ResourceGroupName = match.group(3)
        logger.debug("{get_azure_resource} resource group %s", resource.ResourceGroupName)

    if len(match.groups()) > 6:
        resource.ResourceName = match.group(6)
        logger.debug("{get_azure_resource} resource name %s", resource.ResourceName)

    if len(match.groups()) > 7 and match.group(7):
        splits = match.group(7).split("/")
        logger.debug("{get_azure_resource} splitting subtypes '%s' (%s)", match.group(7), len(splits))
        i = 1 # the string starts with / so the first split is empty
        while i < len(splits) - 1:
            logger.debug("{get_azure_resource} creating subresource with type %s and name %s", splits[i], splits[i+1])
            subRes = AzureSubResource()
            subRes.Type = splits[i]
            subRes.Name = splits[i+1]
            resource.SubResources.append(subRes)
            i += 2

    return resource

def azure_dep_versions(v):
    return tuple(map(int, (v.split("."))))

# Do azure API call to list all virtual machines in a resource group
def get_vm_list(compute_client,rgName):
    return compute_client.virtual_machines.list(rgName)

# Do azue API call to shutdown a virtual machine
def do_vm_power_off(compute_client,rgName,vmName, skipShutdown):
    started = time.monotonic()
    try:
        # Version is not available in azure-mgmt-compute version 14.0.0 until 27.2.0
        from azure.mgmt.compute import __version__
    except ImportError:
        __version__ = "0.0.0"

    # use different implementation call based on used version
    logger.debug("operation=power_off vm=%s resource_group=%s sdk_version=%s skip_shutdown=%s outcome=request_started", vmName, rgName, __version__, skipShutdown)
    if (azure_dep_versions(__version__) == azure_dep_versions(AZURE_COMPUTE_VERSION_5)):
        logger.debug("operation=power_off sdk_method=power_off reason=legacy_sdk")
        compute_client.virtual_machines.power_off(rgName, vmName, skip_shutdown=skipShutdown)
        logger.debug("operation=power_off vm=%s outcome=request_returned state_confirmed=false elapsed_ms=%s", vmName, int((time.monotonic() - started) * 1000))
        return

    logger.debug("operation=power_off sdk_method=begin_power_off")
    compute_client.virtual_machines.begin_power_off(rgName, vmName, skip_shutdown=skipShutdown)
    logger.debug("operation=power_off vm=%s outcome=request_submitted state_confirmed=false elapsed_ms=%s", vmName, int((time.monotonic() - started) * 1000))

# Do azure API call to start a virtual machine
def do_vm_start(compute_client,rgName,vmName):
    started = time.monotonic()
    try:
        # Version is not available in azure-mgmt-compute version 14.0.0 until 27.2.0
        from azure.mgmt.compute import __version__
    except ImportError:
        __version__ = "0.0.0"

    # use different implementation call based on used version
    logger.debug("operation=start vm=%s resource_group=%s sdk_version=%s outcome=request_started", vmName, rgName, __version__)
    if (azure_dep_versions(__version__) == azure_dep_versions(AZURE_COMPUTE_VERSION_5)):
        logger.debug("operation=start sdk_method=start reason=legacy_sdk")
        compute_client.virtual_machines.start(rgName, vmName)
        logger.debug("operation=start vm=%s outcome=request_returned state_confirmed=false elapsed_ms=%s", vmName, int((time.monotonic() - started) * 1000))
        return

    logger.debug("operation=start sdk_method=begin_start")
    compute_client.virtual_machines.begin_start(rgName, vmName)
    logger.debug("operation=start vm=%s outcome=request_submitted state_confirmed=false elapsed_ms=%s", vmName, int((time.monotonic() - started) * 1000))

def get_vm_resource(compute_client, rgName, vmName):
    started = time.monotonic()
    try:
        # Version is not available in azure-mgmt-compute version 14.0.0 until 27.2.0
        from azure.mgmt.compute import __version__
    except ImportError:
        __version__ = "0.0.0"

    # use different implementation call based on used version
    logger.debug("operation=get_vm vm=%s resource_group=%s sdk_version=%s outcome=request_started", vmName, rgName, __version__)
    if (azure_dep_versions(__version__) <= azure_dep_versions(AZURE_RHEL8_COMPUTE_VERSION)):
        resource = compute_client.virtual_machines.get(rgName, vmName, "instanceView")
    else:
        resource = compute_client.virtual_machines.get(resource_group_name=rgName, vm_name=vmName,expand="instanceView")
    logger.debug("operation=get_vm vm=%s outcome=response_received elapsed_ms=%s", vmName, int((time.monotonic() - started) * 1000))
    return resource


def get_fence_subnet_for_config(ipConfig, network_client):
    subnetResource = get_azure_resource(ipConfig.subnet.id)
    logger.debug("{get_fence_subnet_for_config} testing virtual network %s in resource group %s for a fence subnet", subnetResource.ResourceName, subnetResource.ResourceGroupName)
    vnet = network_client.virtual_networks.get(subnetResource.ResourceGroupName, subnetResource.ResourceName)
    return get_subnet(vnet, FENCE_SUBNET_NAME)

def get_subnet(vnet, subnetName):
    for avSubnet in vnet.subnets:
        logger.debug("{get_subnet} searching subnet %s testing subnet %s", subnetName, avSubnet.name)
        if (avSubnet.name.lower() == subnetName.lower()):
            logger.debug("operation=get_subnet subnet=%s outcome=found", avSubnet.name)
            return avSubnet

def test_fence_subnet(fenceSubnet, nic, network_client):
    logger.info("{test_fence_subnet}")
    testOk = True
    if not fenceSubnet:
        testOk = False
        logger.info("{test_fence_subnet} No fence subnet found for virtual network of network interface %s", nic.id)
    else:
        if not fenceSubnet.network_security_group:
            testOk = False
            logger.info("{test_fence_subnet} Fence subnet %s has not network security group", fenceSubnet.id)
        else:
            nsgResource = get_azure_resource(fenceSubnet.network_security_group.id)
            logger.info("{test_fence_subnet} Getting network security group %s in resource group %s", nsgResource.ResourceName, nsgResource.ResourceGroupName)
            nsg = network_client.network_security_groups.get(nsgResource.ResourceGroupName, nsgResource.ResourceName)
            inboundRule = get_inbound_rule_for_nsg(nsg)
            outboundRule = get_outbound_rule_for_nsg(nsg)
            if not outboundRule:
                testOk = False
                logger.info("{test_fence_subnet} Network Securiy Group %s of fence subnet %s has no outbound security rule that blocks all traffic", nsgResource.ResourceName, fenceSubnet.id)
            elif not inboundRule:
                testOk = False
                logger.info("{test_fence_subnet} Network Securiy Group %s of fence subnet %s has no inbound security rule that blocks all traffic", nsgResource.ResourceName, fenceSubnet.id)

    return testOk

def get_inbound_rule_for_nsg(nsg):
    return get_rule_for_nsg(nsg, FENCE_INBOUND_RULE_NAME, FENCE_INBOUND_RULE_DIRECTION)

def get_outbound_rule_for_nsg(nsg):
    return get_rule_for_nsg(nsg, FENCE_OUTBOUND_RULE_NAME, FENCE_OUTBOUND_RULE_DIRECTION)

def get_rule_for_nsg(nsg, ruleName, direction):
    logger.info("{get_rule_for_nsg} Looking for security rule %s with direction %s", ruleName, direction)
    if not nsg:
        logger.info("{get_rule_for_nsg} Network security group not set")
        return None

    for rule in nsg.security_rules:
        logger.info("{get_rule_for_nsg} Testing a %s securiy rule %s", rule.direction, rule.name)
        if (rule.access == "Deny") and (rule.direction == direction)  \
                and (rule.source_port_range == "*") and (rule.destination_port_range == "*") \
                and (rule.protocol == "*") and (rule.destination_address_prefix == "*") \
                and (rule.source_address_prefix == "*") and (rule.provisioning_state == "Succeeded") \
                and (rule.priority == 100) and (rule.name == ruleName):
            logger.info("{get_rule_for_nsg} %s rule found", direction)
            return rule

    return None

def get_network_state(compute_client, network_client, rgName, vmName):
    started = time.monotonic()
    result = FENCE_STATE_ON
    checked_nics = 0
    checked_configs = 0
    logger.debug("operation=get_network_state vm=%s resource_group=%s outcome=started", vmName, rgName)

    try:
        vm = get_vm_resource(compute_client, rgName, vmName)

        allNICOK = True
        for nicRef in vm.network_profile.network_interfaces:
            checked_nics += 1
            nicresource = get_azure_resource(nicRef.id)
            nic = network_client.network_interfaces.get(nicresource.ResourceGroupName, nicresource.ResourceName)
            for ipConfig in nic.ip_configurations:
                checked_configs += 1
                logger.info("{get_network_state} Testing ip configuration %s", ipConfig.name)
                fenceSubnet = get_fence_subnet_for_config(ipConfig, network_client)
                testOk = test_fence_subnet(fenceSubnet, nic, network_client)
                if not testOk:
                    logger.debug("operation=get_network_state vm=%s nic=%s ip_config=%s decision=not_isolated reason=invalid_fence_subnet", vmName, nicresource.ResourceName, ipConfig.name)
                    allNICOK = False
                elif fenceSubnet.id.lower() != ipConfig.subnet.id.lower():
                    logger.debug("operation=get_network_state vm=%s nic=%s ip_config=%s decision=not_isolated reason=subnet_mismatch", vmName, nicresource.ResourceName, ipConfig.name)
                    logger.info("{get_network_state} IP configuration %s is not in fence subnet (ip subnet: %s, fence subnet: %s)", ipConfig.name, ipConfig.subnet.id.lower(), fenceSubnet.id.lower())
                    allNICOK = False
        if allNICOK:
            logger.info("{get_network_state} All IP configurations of all network interfaces are in the fence subnet. Declaring VM as off")
            result = FENCE_STATE_OFF
    except Exception as e:
        logger.debug("operation=get_network_state vm=%s resource_group=%s outcome=failed elapsed_ms=%s", vmName, rgName, int((time.monotonic() - started) * 1000), exc_info=True)
        fail_usage(f"{{get_network_state}} Failed: {e}")

    logger.debug("operation=get_network_state vm=%s reported_state=%s checked_nics=%s checked_ip_configs=%s elapsed_ms=%s", vmName, result, checked_nics, checked_configs, int((time.monotonic() - started) * 1000))
    return result

def set_network_state(compute_client, network_client, rgName, vmName, operation):
    import msrestazure.azure_exceptions
    started = time.monotonic()
    logger.info("{set_network_state} Setting state %s for  %s in resource group %s", operation, vmName, rgName)

    vm = get_vm_resource(compute_client,rgName, vmName)

    operations = []
    for nicRef in vm.network_profile.network_interfaces:
        for attempt in range(MAX_RETRY):
            attempt_started = time.monotonic()
            logger.debug("operation=update_nic vm=%s nic=%s network_operation=%s attempt=%s/%s outcome=started", vmName, nicRef.id, operation, attempt + 1, MAX_RETRY)
            try:
                nicresource = get_azure_resource(nicRef.id)
                nic = network_client.network_interfaces.get(nicresource.ResourceGroupName, nicresource.ResourceName)

                if not nic.tags and operation == "block":
                    nic.tags = {}

                logger.info("{set_network_state} Searching for tags required to unfence this virtual machine")
                for ipConfig in nic.ip_configurations:
                    if operation == "block":
                        fenceSubnet = get_fence_subnet_for_config(ipConfig, network_client)
                        testOk = test_fence_subnet(fenceSubnet, nic, network_client)
                        if testOk:
                            logger.debug("operation=update_nic vm=%s nic=%s ip_config=%s decision=move_to_fence_subnet", vmName, nicresource.ResourceName, ipConfig.name)
                            logger.info("{set_network_state} Changing subnet of ip config of nic %s", nic.id)
                            nic.tags[(f"{FENCE_TAG_SUBNET_ID}_{ipConfig.name}")] = ipConfig.subnet.id
                            nic.tags[(f"{FENCE_TAG_IP_TYPE}_{ipConfig.name}")] = ipConfig.private_ip_allocation_method
                            nic.tags[(f"{FENCE_TAG_IP}_{ipConfig.name}")] = ipConfig.private_ip_address
                            ipConfig.subnet = fenceSubnet
                            ipConfig.private_ip_allocation_method = IP_TYPE_DYNAMIC
                        else:
                            fail_usage(f"{{set_network_state}} Network interface id {nic.id} does not have a network security group.")
                    elif operation == "unblock":
                        if not nic.tags:
                            fail_usage(f"{{set_network_state}} IP configuration {ipConfig.name} is missing the required resource tags (empty)")

                        subnetId = nic.tags.pop(f"{FENCE_TAG_SUBNET_ID}_{ipConfig.name}")
                        ipType = nic.tags.pop(f"{FENCE_TAG_IP_TYPE}_{ipConfig.name}")
                        ipAddress = nic.tags.pop(f"{FENCE_TAG_IP}_{ipConfig.name}")

                        if (subnetId and ipType and (ipAddress or (ipType.lower() == IP_TYPE_DYNAMIC.lower()))):
                            logger.info("{set_network_state} tags found (subnetId: %s, ipType: %s, ipAddress: %s)", subnetId, ipType, ipAddress)

                            subnetResource = get_azure_resource(subnetId)
                            vnet = network_client.virtual_networks.get(subnetResource.ResourceGroupName, subnetResource.ResourceName)
                            logger.info("{set_network_state} looking for subnet %s", len(subnetResource.SubResources))
                            oldSubnet = get_subnet(vnet, subnetResource.SubResources[0].Name)
                            if not oldSubnet:
                                fail_usage(f"{{set_network_state}} subnet {subnetId} not found")

                            logger.debug("operation=update_nic vm=%s nic=%s ip_config=%s decision=restore_original_subnet", vmName, nicresource.ResourceName, ipConfig.name)
                            ipConfig.subnet = oldSubnet
                            ipConfig.private_ip_allocation_method = ipType
                            if ipAddress:
                                ipConfig.private_ip_address = ipAddress
                        else:
                            fail_usage(f"{{set_network_state}} IP configuration {ipConfig.name} is missing the required resource tags(subnetId: {subnetId}, ipType: {ipType}, ipAddress: {ipAddress})")

                logger.info("{set_network_state} updating nic %s", nic.id)
                op = network_client.network_interfaces.create_or_update(nicresource.ResourceGroupName, nicresource.ResourceName, nic)
                operations.append(op)
                logger.debug("operation=update_nic vm=%s nic=%s attempt=%s/%s outcome=request_submitted state_confirmed=false elapsed_ms=%s", vmName, nicresource.ResourceName, attempt + 1, MAX_RETRY, int((time.monotonic() - attempt_started) * 1000))
                break
            except msrestazure.azure_exceptions.CloudError as cex:
                logger.log(logging.ERROR if attempt + 1 == MAX_RETRY else logging.WARNING,
                           "operation=update_nic vm=%s nic=%s network_operation=%s attempt=%s/%s outcome=%s error_type=%s error_code=%s retry_wait_s=%s elapsed_ms=%s",
                           vmName, nicRef.id, operation, attempt + 1, MAX_RETRY,
                           "retry_exhausted" if attempt + 1 == MAX_RETRY else "retry_wait",
                           type(cex).__name__, getattr(getattr(cex, "error", None), "error", None), RETRY_WAIT,
                           int((time.monotonic() - attempt_started) * 1000))
                if cex.error and cex.error.error and cex.error.error.lower() == "PrivateIPAddressIsBeingCleanedUp":
                    logger.error("{set_network_state} PrivateIPAddressIsBeingCleanedUp")
                time.sleep(RETRY_WAIT)

            except Exception:
                logger.exception("operation=update_nic vm=%s nic=%s network_operation=%s attempt=%s/%s outcome=failed elapsed_ms=%s", vmName, nicRef.id, operation, attempt + 1, MAX_RETRY, int((time.monotonic() - attempt_started) * 1000))
                break

    logger.debug("operation=set_network_state vm=%s resource_group=%s network_operation=%s submitted_updates=%s state_confirmed=false elapsed_ms=%s", vmName, rgName, operation, len(operations), int((time.monotonic() - started) * 1000))

def get_azure_config(options):
    config = AzureConfiguration()

    config.RGName = options.get("--resourceGroup")
    config.VMName = options.get("--plug")
    config.SubscriptionId = options.get("--subscriptionId")
    config.Cloud = options.get("--cloud")
    config.MetadataEndpoint = options.get("--metadata-endpoint")
    config.UseMSI = "--msi" in options
    config.Tenantid = options.get("--tenantId")
    config.ApplicationId = options.get("--username")
    config.ApplicationKey = options.get("--password")
    config.Verbose = options.get("--verbose")

    if not config.RGName:
        logger.info("resourceGroup not provided. Using metadata service")
        config.RGName = get_from_metadata("resourceGroupName")

    if not config.SubscriptionId:
        logger.info("subscriptionId not provided. Using metadata service")
        config.SubscriptionId = get_from_metadata("subscriptionId")

    logger.debug("operation=configuration resource_group=%s vm=%s cloud=%s auth_method=%s resource_group_source=%s subscription_source=%s", config.RGName, config.VMName, config.Cloud or AZURE_CLOUD_PUBLIC, "managed_identity" if config.UseMSI else "service_principal", "option" if options.get("--resourceGroup") else "imds", "option" if options.get("--subscriptionId") else "imds")
    return config

# Function to fetch endpoints from metadata endpoint for azure_stack
def get_cloud_from_arm_metadata_endpoint(arm_endpoint):
    import requests

    started = time.monotonic()
    logger.debug("operation=arm_metadata outcome=request_started")
    try:
        session = requests.Session()
        metadata_endpoint = arm_endpoint + "/metadata/endpoints?api-version=2015-01-01"
        response = session.get(metadata_endpoint)
        logger.debug("operation=arm_metadata http_status=%s outcome=response_received elapsed_ms=%s", response.status_code, int((time.monotonic() - started) * 1000))
        if response.status_code == 200:
            metadata = response.json()
            return {
                "resource_manager": arm_endpoint,
                "credential_scopes": [metadata.get("graphEndpoint") + "/.default"],
                "authority_hosts":  metadata['authentication'].get('loginEndpoint').replace("https://","")
            }
        else:
            fail_usage(f"Failed to get cloud from metadata endpoint: {arm_endpoint} - HTTP {response.status_code}")
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as e:
        logger.debug("operation=arm_metadata outcome=failed error_type=%s elapsed_ms=%s", type(e).__name__, int((time.monotonic() - started) * 1000))
        fail_usage(f"Failed to get cloud from metadata endpoint: {arm_endpoint} - {e}")

def get_azure_arm_endpoints(cloudName, authority):
    cloudEnvironment = {
        "authority_hosts": authority
    }

    if cloudName == AZURE_CLOUD_NAME_CHINA:
        cloudEnvironment["resource_manager"] = "https://management.chinacloudapi.cn/"
        cloudEnvironment["credential_scopes"] = ["https://management.chinacloudapi.cn/.default"]
        return cloudEnvironment

    if cloudName == AZURE_CLOUD_NAME_US_GOV:
        cloudEnvironment["resource_manager"] = "https://management.usgovcloudapi.net/"
        cloudEnvironment["credential_scopes"] = ["https://management.core.usgovcloudapi.net/.default"]
        return cloudEnvironment

    if cloudName == AZURE_CLOUD_NAME_PUBLIC:
        cloudEnvironment["resource_manager"] = "https://management.azure.com/"
        cloudEnvironment["credential_scopes"] = ["https://management.azure.com/.default"]
        return cloudEnvironment


def get_azure_cloud_environment(config):
    if (config.Cloud is None):
        config.Cloud = AZURE_CLOUD_PUBLIC

    try:
        from azure.identity import AzureAuthorityHosts

        logger.debug("operation=cloud_selection cloud=%s sdk=azure.identity", config.Cloud)
        azureCloudName = AZURE_CLOUD_NAME_PUBLIC
        authorityHosts = AzureAuthorityHosts.AZURE_PUBLIC_CLOUD
        if (config.Cloud.lower() == AZURE_CLOUD_CHINA):
            azureCloudName = AZURE_CLOUD_NAME_CHINA
            authorityHosts = AzureAuthorityHosts.AZURE_CHINA
        elif (config.Cloud.lower() == AZURE_CLOUD_US_GOV):
            azureCloudName = AZURE_CLOUD_NAME_US_GOV
            authorityHosts = AzureAuthorityHosts.AZURE_GOVERNMENT
        elif (config.Cloud.lower() == AZURE_CLOUD_STACK):
            # use custom function to call the azuer stack metadata endpoint to get required configuration.
            return get_cloud_from_arm_metadata_endpoint(config.MetadataEndpoint)

        return get_azure_arm_endpoints(azureCloudName, authorityHosts)

    except ImportError:
        logger.debug("operation=cloud_selection cloud=%s sdk=msrestazure reason=legacy_sdk", config.Cloud)
        if (config.Cloud.lower() == AZURE_CLOUD_PUBLIC):
            from msrestazure.azure_cloud import AZURE_PUBLIC_CLOUD
            cloud_environment = AZURE_PUBLIC_CLOUD
        elif (config.Cloud.lower() == AZURE_CLOUD_CHINA):
            from msrestazure.azure_cloud import AZURE_CHINA_CLOUD
            cloud_environment = AZURE_CHINA_CLOUD
        elif (config.Cloud.lower() == AZURE_CLOUD_GERMANY):
            from msrestazure.azure_cloud import AZURE_GERMAN_CLOUD
            cloud_environment = AZURE_GERMAN_CLOUD
        elif (config.Cloud.lower() == AZURE_CLOUD_US_GOV):
            from msrestazure.azure_cloud import AZURE_US_GOV_CLOUD
            cloud_environment = AZURE_US_GOV_CLOUD
        elif (config.Cloud.lower() == AZURE_CLOUD_STACK):
            from msrestazure.azure_cloud import get_cloud_from_metadata_endpoint
            cloud_environment = get_cloud_from_metadata_endpoint(config.MetadataEndpoint)

        authority_hosts = cloud_environment.endpoints.active_directory.replace("http://","")
        return {
            "resource_manager": cloud_environment.endpoints.resource_manager,
            "credential_scopes": [cloud_environment.endpoints.active_directory_resource_id + "/.default"],
            "authority_hosts": authority_hosts,
            "cloud_environment": cloud_environment,
        }

def get_azure_credentials(config):
    credentials = None
    cloud_environment = get_azure_cloud_environment(config)
    if config.UseMSI:
        try:
            from azure.identity import ManagedIdentityCredential
            logger.debug("operation=credentials auth_method=managed_identity implementation=ManagedIdentityCredential")
            credentials = ManagedIdentityCredential(authority=cloud_environment["authority_hosts"])
        except ImportError:
            from msrestazure.azure_active_directory import MSIAuthentication
            logger.debug("operation=credentials auth_method=managed_identity implementation=MSIAuthentication reason=legacy_sdk")
            credentials = MSIAuthentication(cloud_environment=cloud_environment["cloud_environment"])
        return credentials

    try:
        # try to use new libraries ClientSecretCredential (azure.identity, based on azure.core)
        from azure.identity import ClientSecretCredential
        logger.debug("operation=credentials auth_method=service_principal implementation=ClientSecretCredential")
        credentials = ClientSecretCredential(
            client_id = config.ApplicationId,
            client_secret = config.ApplicationKey,
            tenant_id = config.Tenantid,
            authority=cloud_environment["authority_hosts"]
        )
    except ImportError:
         # use old libraries ServicePrincipalCredentials (azure.common) if new one is not available
        from azure.common.credentials import ServicePrincipalCredentials
        logger.debug("operation=credentials auth_method=service_principal implementation=ServicePrincipalCredentials reason=legacy_sdk")
        credentials = ServicePrincipalCredentials(
            client_id = config.ApplicationId,
            secret = config.ApplicationKey,
            tenant = config.Tenantid,
            cloud_environment=cloud_environment["cloud_environment"]
        )

    return credentials

def get_azure_compute_client(config):
    from azure.mgmt.compute import ComputeManagementClient

    started = time.monotonic()
    cloud_environment = get_azure_cloud_environment(config)
    credentials = get_azure_credentials(config)

    # Try to read the default used api version from the installed package.
    try:
        compute_api_version = ComputeManagementClient.LATEST_PROFILE.get_profile_dict()["azure.mgmt.compute.ComputeManagementClient"]["virtual_machines"]
    except (AttributeError, KeyError, TypeError) as e:
        compute_api_version = ComputeManagementClient.DEFAULT_API_VERSION
        logger.debug("{get_azure_compute_client} Failed to get the latest profile: %s using the default api version %s", e, compute_api_version)

    logger.debug("{get_azure_compute_client} use virtual_machine api version: %s", compute_api_version)

    if (config.Cloud.lower() == AZURE_CLOUD_STACK) and not config.MetadataEndpoint:
            fail_usage("metadata-endpoint not specified")

    try:
        from azure.profiles import KnownProfiles
        if (config.Cloud.lower() == AZURE_CLOUD_STACK):
            client_profile = KnownProfiles.v2020_09_01_hybrid
        else:
            client_profile = KnownProfiles.default
        compute_client = ComputeManagementClient(
            credentials,
            config.SubscriptionId,
            base_url=cloud_environment["resource_manager"],
            profile=client_profile,
            credential_scopes=cloud_environment["credential_scopes"],
            api_version=compute_api_version
        )
    except TypeError:
        logger.debug("operation=compute_client reason=constructor_compatibility_fallback")
        compute_client = ComputeManagementClient(
            credentials,
            config.SubscriptionId,
            base_url=cloud_environment["resource_manager"],
            api_version=compute_api_version
        )

    logger.debug("operation=compute_client cloud=%s api_version=%s outcome=client_created elapsed_ms=%s", config.Cloud, compute_api_version, int((time.monotonic() - started) * 1000))
    return compute_client

def get_azure_network_client(config):
    from azure.mgmt.network import NetworkManagementClient

    started = time.monotonic()
    cloud_environment = get_azure_cloud_environment(config)
    credentials = get_azure_credentials(config)

    if (config.Cloud.lower() == AZURE_CLOUD_STACK) and not config.MetadataEndpoint:
        fail_usage("metadata-endpoint not specified")


    from azure.profiles import KnownProfiles

    if (config.Cloud.lower() == AZURE_CLOUD_STACK):
        client_profile = KnownProfiles.v2020_09_01_hybrid
    else:
        client_profile = KnownProfiles.default

    try:
        network_client = NetworkManagementClient(
            credentials,
            config.SubscriptionId,
            base_url=cloud_environment["resource_manager"],
            profile=client_profile,
            credential_scopes=cloud_environment["credential_scopes"],
            api_version=NETWORK_MGMT_CLIENT_API_VERSION
        )
    except TypeError:
        logger.debug("operation=network_client reason=constructor_compatibility_fallback")
        network_client = NetworkManagementClient(
            credentials,
            config.SubscriptionId,
            base_url=cloud_environment["resource_manager"],
            api_version=NETWORK_MGMT_CLIENT_API_VERSION
        )
    logger.debug("operation=network_client cloud=%s api_version=%s outcome=client_created elapsed_ms=%s", config.Cloud, NETWORK_MGMT_CLIENT_API_VERSION, int((time.monotonic() - started) * 1000))
    return network_client
