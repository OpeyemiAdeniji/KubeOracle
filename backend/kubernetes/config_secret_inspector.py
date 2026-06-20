from loguru import logger
from kubernetes.kubectl_executor import run_kubectl


def inspect_config_secrets() -> dict:
    logger.info("Inspecting ConfigMap and Secret references...")
    pods_result = run_kubectl(["get", "pods", "-A", "-o", "json"], parse_json=True)
    cm_result = run_kubectl(["get", "configmaps", "-A", "-o", "json"], parse_json=True)
    secret_result = run_kubectl(["get", "secrets", "-A", "-o", "json"], parse_json=True)

    if not pods_result.success or not pods_result.parsed:
        return {"success": False, "error": pods_result.stderr}

    # Build lookup: (namespace, name) -> set of keys
    existing_cms: dict[tuple, set] = {}
    for cm in (cm_result.parsed or {}).get("items", []):
        ns = cm["metadata"]["namespace"]
        name = cm["metadata"]["name"]
        existing_cms[(ns, name)] = set(cm.get("data", {}).keys())

    existing_secrets: dict[tuple, set] = {}
    for secret in (secret_result.parsed or {}).get("items", []):
        ns = secret["metadata"]["namespace"]
        name = secret["metadata"]["name"]
        existing_secrets[(ns, name)] = set(secret.get("data", {}).keys())

    issues = []

    for pod in pods_result.parsed.get("items", []):
        ns = pod["metadata"]["namespace"]
        pod_name = pod["metadata"]["name"]
        pod_spec = pod.get("spec", {})

        for container in pod_spec.get("containers", []):
            # envFrom references
            for env_from in container.get("envFrom", []):
                cm_ref = env_from.get("configMapRef", {})
                if cm_ref and not cm_ref.get("optional", False):
                    cm_name = cm_ref.get("name", "")
                    if (ns, cm_name) not in existing_cms:
                        issues.append(_issue("missing_configmap", pod_name, ns, cm_name,
                                             f"ConfigMap '{cm_name}' used in envFrom not found in namespace '{ns}'"))

                secret_ref = env_from.get("secretRef", {})
                if secret_ref and not secret_ref.get("optional", False):
                    sec_name = secret_ref.get("name", "")
                    if (ns, sec_name) not in existing_secrets:
                        issues.append(_issue("missing_secret", pod_name, ns, sec_name,
                                             f"Secret '{sec_name}' used in envFrom not found in namespace '{ns}'"))

            # env valueFrom references
            for env in container.get("env", []):
                value_from = env.get("valueFrom", {})

                cmkr = value_from.get("configMapKeyRef", {})
                if cmkr and not cmkr.get("optional", False):
                    cm_name = cmkr.get("name", "")
                    key = cmkr.get("key", "")
                    if (ns, cm_name) not in existing_cms:
                        issues.append(_issue("missing_configmap", pod_name, ns, cm_name,
                                             f"ConfigMap '{cm_name}' referenced by env var not found"))
                    elif key and key not in existing_cms.get((ns, cm_name), set()):
                        issues.append(_issue("missing_configmap_key", pod_name, ns, f"{cm_name}.{key}",
                                             f"Key '{key}' not found in ConfigMap '{cm_name}'"))

                skr = value_from.get("secretKeyRef", {})
                if skr and not skr.get("optional", False):
                    sec_name = skr.get("name", "")
                    key = skr.get("key", "")
                    if (ns, sec_name) not in existing_secrets:
                        issues.append(_issue("missing_secret", pod_name, ns, sec_name,
                                             f"Secret '{sec_name}' referenced by env var not found"))
                    elif key and key not in existing_secrets.get((ns, sec_name), set()):
                        issues.append(_issue("missing_secret_key", pod_name, ns, f"{sec_name}.{key}",
                                             f"Key '{key}' not found in Secret '{sec_name}'"))

        # Volume references
        for volume in pod_spec.get("volumes", []):
            cm_vol = volume.get("configMap", {})
            if cm_vol:
                cm_name = cm_vol.get("name", "")
                if cm_name and (ns, cm_name) not in existing_cms:
                    issues.append(_issue("missing_configmap_volume", pod_name, ns, cm_name,
                                         f"ConfigMap volume '{cm_name}' not found in namespace '{ns}'"))

            sec_vol = volume.get("secret", {})
            if sec_vol:
                sec_name = sec_vol.get("secretName", "")
                if sec_name and (ns, sec_name) not in existing_secrets:
                    issues.append(_issue("missing_secret_volume", pod_name, ns, sec_name,
                                         f"Secret volume '{sec_name}' not found in namespace '{ns}'"))

    return {
        "success": True,
        "total_configmaps": len(existing_cms),
        "total_secrets": len(existing_secrets),
        "issues_found": len(issues) > 0,
        "issues": issues,
    }


def _issue(issue_type: str, pod: str, namespace: str, ref: str, message: str) -> dict:
    return {
        "type": issue_type,
        "pod": pod,
        "namespace": namespace,
        "ref": ref,
        "message": message,
    }
