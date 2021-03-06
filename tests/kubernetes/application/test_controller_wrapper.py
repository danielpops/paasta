import mock
from kubernetes.client.rest import ApiException

from paasta_tools.kubernetes.application.controller_wrappers import Application
from paasta_tools.kubernetes.application.controller_wrappers import DeploymentWrapper
from paasta_tools.kubernetes_tools import KubernetesDeploymentConfig


def test_brutal_bounce():
    # mock the new client used to brutal bounce in the background using threading.
    mock_cloned_client = mock.MagicMock()

    with mock.patch(
        "paasta_tools.kubernetes.application.controller_wrappers.KubeClient",
        return_value=mock_cloned_client,
        autospec=True,
    ):
        with mock.patch(
            "paasta_tools.kubernetes.application.controller_wrappers.threading.Thread",
            autospec=True,
        ) as mock_deep_delete_and_create:
            mock_client = mock.MagicMock()

            app = mock.MagicMock()
            app.item.metadata.name = "fake_name"
            app.item.metadata.namespace = "faasta"

            # we do NOT call deep_delete_and_create
            app = setup_app({}, True)
            DeploymentWrapper.update(self=app, kube_client=mock_client)

            assert mock_deep_delete_and_create.call_count == 0

            # we call deep_delete_and_create: when bounce_method is brutal
            config_dict = {"instances": 1, "bounce_method": "brutal"}

            app = setup_app(config_dict, True)
            app.update(kube_client=mock_client)

            mock_deep_delete_and_create.assert_called_once_with(
                target=app.deep_delete_and_create, args=[mock_cloned_client]
            )


def test_ensure_pod_disruption_budget_create():
    with mock.patch(
        "paasta_tools.kubernetes.application.controller_wrappers.pod_disruption_budget_for_service_instance",
        autospec=True,
    ) as mock_pdr_for_service_instance:
        mock_req_pdr = mock.Mock()
        mock_req_pdr.spec.max_unavailable = 10
        mock_pdr_for_service_instance.return_value = mock_req_pdr

        mock_client = mock.MagicMock()

        mock_client.policy.read_namespaced_pod_disruption_budget.side_effect = ApiException(
            status=404
        )

        app = mock.MagicMock()
        app.soa_config.get_bounce_margin_factor.return_value = 0.1
        app.kube_deployment.service.return_value = "fake_service"
        app.kube_deployment.instance.return_value = "fake_instance"
        Application.ensure_pod_disruption_budget(self=app, kube_client=mock_client)
        mock_client.policy.create_namespaced_pod_disruption_budget.assert_called_once_with(
            namespace="paasta", body=mock_req_pdr
        )


def test_ensure_pod_disruption_budget_replaces_outdated():
    with mock.patch(
        "paasta_tools.kubernetes.application.controller_wrappers.pod_disruption_budget_for_service_instance",
        autospec=True,
    ) as mock_pdr_for_service_instance:
        mock_req_pdr = mock.Mock()
        mock_req_pdr.spec.max_unavailable = 10
        mock_pdr_for_service_instance.return_value = mock_req_pdr

        mock_client = mock.MagicMock()

        mock_pdr = mock.Mock()
        mock_pdr.spec.max_unavailable = 5

        mock_client.policy.read_namespaced_pod_disruption_budget.return_value = mock_pdr

        app = mock.MagicMock()
        app.soa_config.get_bounce_margin_factor.return_value = 0.1
        app.kube_deployment.service.return_value = "fake_service"
        app.kube_deployment.instance.return_value = "fake_instance"
        Application.ensure_pod_disruption_budget(self=app, kube_client=mock_client)

        mock_client.policy.patch_namespaced_pod_disruption_budget.assert_called_once_with(
            name=mock_req_pdr.metadata.name,
            namespace=mock_req_pdr.metadata.namespace,
            body=mock_req_pdr,
        )


def setup_app(config_dict, exists_hpa):
    item = mock.MagicMock()
    item.metadata.name = "fake_name"
    item.metadata.namespace = "faasta"

    app = DeploymentWrapper(item=item)
    app.soa_config = KubernetesDeploymentConfig(
        service="service",
        cluster="cluster",
        instance="instance",
        config_dict=config_dict,
        branch_dict=None,
    )

    app.exists_hpa = mock.Mock(return_value=exists_hpa)
    app.delete_horizontal_pod_autoscaler = mock.Mock(return_value=None)
    return app


def test_sync_horizontal_pod_autoscaler_no_autoscaling():
    mock_client = mock.MagicMock()
    # Do nothing
    config_dict = {"instances": 1}
    app = setup_app(config_dict, False)
    app.sync_horizontal_pod_autoscaler(kube_client=mock_client)
    assert (
        mock_client.autoscaling.create_namespaced_horizontal_pod_autoscaler.call_count
        == 0
    )
    assert (
        mock_client.autoscaling.patch_namespaced_horizontal_pod_autoscaler.call_count
        == 0
    )
    assert app.delete_horizontal_pod_autoscaler.call_count == 0


def test_sync_horizontal_pod_autoscaler_delete_hpa_when_no_autoscaling():
    mock_client = mock.MagicMock()
    # old HPA got removed so delete
    config_dict = {"instances": 1}
    app = setup_app(config_dict, True)
    app.sync_horizontal_pod_autoscaler(kube_client=mock_client)
    assert (
        mock_client.autoscaling.create_namespaced_horizontal_pod_autoscaler.call_count
        == 0
    )
    assert (
        mock_client.autoscaling.patch_namespaced_horizontal_pod_autoscaler.call_count
        == 0
    )
    assert app.delete_horizontal_pod_autoscaler.call_count == 1


def test_sync_horizontal_pod_autoscaler_create_hpa():
    mock_client = mock.MagicMock()
    # Create
    config_dict = {"max_instances": 3}
    app = setup_app(config_dict, False)
    app.sync_horizontal_pod_autoscaler(kube_client=mock_client)
    assert (
        mock_client.autoscaling.patch_namespaced_horizontal_pod_autoscaler.call_count
        == 0
    )
    assert app.delete_horizontal_pod_autoscaler.call_count == 0
    mock_client.autoscaling.create_namespaced_horizontal_pod_autoscaler.assert_called_once_with(
        namespace="faasta",
        body=app.soa_config.get_autoscaling_metric_spec("fake_name", "faasta"),
        pretty=True,
    )


def test_sync_horizontal_pod_autoscaler_update_hpa():
    mock_client = mock.MagicMock()
    # Update
    config_dict = {"max_instances": 3}
    app = setup_app(config_dict, True)
    app.sync_horizontal_pod_autoscaler(kube_client=mock_client)
    assert (
        mock_client.autoscaling.create_namespaced_horizontal_pod_autoscaler.call_count
        == 0
    )
    assert app.delete_horizontal_pod_autoscaler.call_count == 0
    mock_client.autoscaling.patch_namespaced_horizontal_pod_autoscaler.assert_called_once_with(
        namespace="faasta",
        name="fake_name",
        body=app.soa_config.get_autoscaling_metric_spec("fake_name", "faasta"),
        pretty=True,
    )


def test_sync_horizontal_pod_autoscaler_bespoke_autoscaler():
    mock_client = mock.MagicMock()

    # Do nothing
    config_dict = {"max_instances": 3, "autoscaling": {"decision_policy": "bespoke"}}
    app = setup_app(config_dict, False)
    app.sync_horizontal_pod_autoscaler(kube_client=mock_client)
    assert (
        mock_client.autoscaling.create_namespaced_horizontal_pod_autoscaler.call_count
        == 0
    )
    assert (
        mock_client.autoscaling.patch_namespaced_horizontal_pod_autoscaler.call_count
        == 0
    )
    assert app.delete_horizontal_pod_autoscaler.call_count == 0
