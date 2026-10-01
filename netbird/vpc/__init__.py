import pulumi
import pulumi_aws as aws


class Network(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        resource_name: str,
        vpc_cidr: str,
        subnet_cidr: str,
        standby_subnet_cidr: str,
        az: str,
        standby_az: str,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("netbird:vpc:Network", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)
        tags = {"Name": resource_name}

        self.vpc = aws.ec2.Vpc(
            "vpc",
            cidr_block=vpc_cidr,
            enable_dns_support=True,
            enable_dns_hostnames=True,
            tags=tags,
            opts=child,
        )
        self.subnet = aws.ec2.Subnet(
            "subnet",
            vpc_id=self.vpc.id,
            cidr_block=subnet_cidr,
            availability_zone=az,
            map_public_ip_on_launch=True,
            tags=tags,
            opts=child,
        )
        self.standby_subnet = aws.ec2.Subnet(
            "standby-subnet",
            vpc_id=self.vpc.id,
            cidr_block=standby_subnet_cidr,
            availability_zone=standby_az,
            map_public_ip_on_launch=True,
            tags={"Name": f"{resource_name}-standby"},
            opts=child,
        )

        igw = aws.ec2.InternetGateway("igw", vpc_id=self.vpc.id, tags=tags, opts=child)
        route_table = aws.ec2.RouteTable(
            "route-table",
            vpc_id=self.vpc.id,
            routes=[{"cidr_block": "0.0.0.0/0", "gateway_id": igw.id}],
            tags=tags,
            opts=child,
        )
        aws.ec2.RouteTableAssociation(
            "subnet-route",
            subnet_id=self.subnet.id,
            route_table_id=route_table.id,
            opts=child,
        )
        aws.ec2.RouteTableAssociation(
            "standby-route",
            subnet_id=self.standby_subnet.id,
            route_table_id=route_table.id,
            opts=child,
        )

        self.vpc_id = self.vpc.id
        self.subnet_id = self.subnet.id
        self.standby_subnet_id = self.standby_subnet.id
        self.register_outputs(
            {
                "vpc_id": self.vpc_id,
                "subnet_id": self.subnet_id,
                "standby_subnet_id": self.standby_subnet_id,
            }
        )
