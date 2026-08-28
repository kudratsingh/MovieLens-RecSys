"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";

import { ErrorState } from "@/components/ui/resource-states";

export class FrontendErrorBoundary extends Component<
  { children: ReactNode; label?: string },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Frontend resource boundary", error, info.componentStack);
  }

  render() {
    if (this.state.failed) {
      return (
        <ErrorState
          label={this.props.label ?? "This section"}
          message="An unexpected interface error occurred. Navigation and other resources remain available."
          // Re-mounting the subtree is a real retry: a render that failed on a
          // transient value — a resource that has since resolved, say — comes
          // back on the second attempt, and one that cannot lands here again.
          onRetry={() => this.setState({ failed: false })}
        />
      );
    }
    return this.props.children;
  }
}
