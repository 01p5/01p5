import { SubpathPortalPage } from "./SubpathPortalPage";

export function GpuPortalPage(): JSX.Element {
  return (
    <SubpathPortalPage
      path="/gpu/"
      title="GPU dashboard"
      enableHint="gpuDashboard.enabled=true"
      testIdPrefix="gpu-portal"
      parentRoute="/capabilities/gpu"
    />
  );
}
