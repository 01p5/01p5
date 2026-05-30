import { SubpathPortalPage } from "./SubpathPortalPage";

export function SlurmPortalPage(): JSX.Element {
  return (
    <SubpathPortalPage
      path="/slurm/"
      title="Slurm dashboard"
      enableHint="slurmDashboard.enabled=true"
      testIdPrefix="slurm-portal"
    />
  );
}
