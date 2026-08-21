type UiPreviewEnvironment = {
  NODE_ENV?: string;
  MOVIELENS_UI_FIXTURE_MODE?: string;
};

export function isolatedUiPreviewMode(
  environment: UiPreviewEnvironment = process.env,
): boolean {
  return (
    environment.NODE_ENV !== "production" &&
    environment.MOVIELENS_UI_FIXTURE_MODE === "1"
  );
}
