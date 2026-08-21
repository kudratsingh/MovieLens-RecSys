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
        />
      );
    }
    return this.props.children;
  }
}
