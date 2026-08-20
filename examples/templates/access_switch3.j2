#!/bin/bash
# Junos Push Configuration - Batch Automation Script
# This script demonstrates automated deployment across multiple groups

set -e  # Exit on any error

echo "🚀 Starting Junos Configuration Batch Deployment"
echo "=================================================="

# Configuration files to deploy
CONFIGS=(
    "examples/interface_config.set"
    "examples/ospf_config.set"
    "examples/security_config.set"
)

# Target groups
GROUPS=("core" "edge" "campus")

# Function to deploy configuration to a group
deploy_config() {
    local group=$1
    local config_file=$2
    local operation=$3

    echo ""
    echo "📋 Deploying $(basename $config_file) to $group group..."
    echo "Operation: $operation"

    if junos-push -g "$group" -c "$config_file" -o "$operation" --backup -v; then
        echo "✅ Successfully deployed to $group"
    else
        echo "❌ Failed to deploy to $group"
        return 1
    fi
}

# Function to perform dry run on all groups
dry_run_all() {
    echo "🧪 Performing dry run on all groups..."

    for group in "${GROUPS[@]}"; do
        for config in "${CONFIGS[@]}"; do
            echo ""
            echo "Testing $(basename $config) on $group..."
            junos-push -g "$group" -c "$config" --dry-run
        done
    done
}

# Function to deploy with commit-confirmed (safer)
deploy_with_confirmation() {
    echo "🛡️ Deploying with commit-confirmed (5 min auto-rollback)..."

    for group in "${GROUPS[@]}"; do
        for config in "${CONFIGS[@]}"; do
            if ! deploy_config "$group" "$config" "commit-confirmed"; then
                echo "💥 Deployment failed, stopping batch operation"
                exit 1
            fi

            # Wait between deployments
            echo "⏳ Waiting 30 seconds before next deployment..."
            sleep 30
        done
    done

    echo ""
    echo "⚠️  Remember to confirm commits within 5 minutes or they will auto-rollback!"
}

# Function to commit all confirmed configurations
commit_all_confirmed() {
    echo "✅ Committing all confirmed configurations..."

    for group in "${GROUPS[@]}"; do
        echo "Committing configurations on $group..."
        if junos-push -g "$group" -c "examples/interface_config.set" -o commit; then
            echo "✅ Committed $group successfully"
        else
            echo "❌ Failed to commit $group"
        fi
    done
}

# Main menu
case "${1:-}" in
    "dry-run")
        dry_run_all
        ;;
    "deploy-confirmed")
        deploy_with_confirmation
        ;;
    "commit-all")
        commit_all_confirmed
        ;;
    "deploy-direct")
        echo "⚠️  Direct deployment without confirmation - USE WITH CAUTION!"
        read -p "Are you sure? Type 'YES' to continue: " confirm
        if [ "$confirm" = "YES" ]; then
            for group in "${GROUPS[@]}"; do
                for config in "${CONFIGS[@]}"; do
                    deploy_config "$group" "$config" "commit"
                done
            done
        else
            echo "Deployment cancelled"
        fi
        ;;
    *)
        echo "Usage: $0 {dry-run|deploy-confirmed|commit-all|deploy-direct}"
        echo ""
        echo "Commands:"
        echo "  dry-run         - Test all configurations without changes"
        echo "  deploy-confirmed - Deploy with 5-minute auto-rollback"
        echo "  commit-all      - Commit all pending confirmed configurations"
        echo "  deploy-direct   - Deploy directly (dangerous, requires confirmation)"
        echo ""
        echo "Examples:"
        echo "  $0 dry-run"
        echo "  $0 deploy-confirmed"
        exit 1
        ;;
esac

echo ""
echo "🎉 Batch operation completed!"
