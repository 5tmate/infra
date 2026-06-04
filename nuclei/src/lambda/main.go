package main

import (
	"context"
	"log"
	"os"
	"strings"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/ecs"
	ecstypes "github.com/aws/aws-sdk-go-v2/service/ecs/types"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

func main() {
	cfg, err := config.LoadDefaultConfig(context.Background())
	if err != nil {
		log.Fatal(err)
	}
	client := ecs.NewFromConfig(cfg)

	r := gin.Default()
	r.POST("/", func(c *gin.Context) {
		var req struct {
			Target    string   `json:"target"    binding:"required"`
			Templates []string `json:"templates"`
			Tags      []string `json:"tags"`
			Capacity  string   `json:"capacity"  binding:"required,oneof=FARGATE FARGATE_SPOT"`
		}
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(400, gin.H{"error": err.Error()})
			return
		}
		if (len(req.Templates) == 0) == (len(req.Tags) == 0) {
			c.JSON(400, gin.H{"error": "exactly one of 'templates' or 'tags' is required"})
			return
		}

		runID := uuid.NewString()
		out, err := client.RunTask(c.Request.Context(), &ecs.RunTaskInput{
			Cluster:        aws.String(os.Getenv("CLUSTER_ARN")),
			TaskDefinition: aws.String(os.Getenv("TASK_DEFINITION_ARN")),
			CapacityProviderStrategy: []ecstypes.CapacityProviderStrategyItem{
				{CapacityProvider: aws.String(req.Capacity), Weight: 1},
			},
			NetworkConfiguration: &ecstypes.NetworkConfiguration{
				AwsvpcConfiguration: &ecstypes.AwsVpcConfiguration{
					Subnets:        strings.Split(os.Getenv("SUBNETS"), ","),
					SecurityGroups: strings.Split(os.Getenv("SECURITY_GROUPS"), ","),
					AssignPublicIp: ecstypes.AssignPublicIpEnabled,
				},
			},
			Overrides: &ecstypes.TaskOverride{
				ContainerOverrides: []ecstypes.ContainerOverride{{
					Name: aws.String(os.Getenv("CONTAINER_NAME")),
					Environment: []ecstypes.KeyValuePair{
						{Name: aws.String("RUN_ID"), Value: aws.String(runID)},
						{Name: aws.String("TARGET"), Value: aws.String(req.Target)},
						{Name: aws.String("TEMPLATES"), Value: aws.String(strings.Join(req.Templates, ","))},
						{Name: aws.String("TAGS"), Value: aws.String(strings.Join(req.Tags, ","))},
					},
				}},
			},
		})
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}

		c.JSON(202, gin.H{"run_id": runID, "task_arn": *out.Tasks[0].TaskArn})
	})

	r.Run()
}
