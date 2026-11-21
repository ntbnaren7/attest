import { Component, type ReactNode } from 'react'

/** Contains a rendering error to one part of the page instead of blanking the whole dashboard. */
export class Guard extends Component<{ children: ReactNode; label?: string }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  componentDidCatch(error: unknown) {
    console.error('UI part failed:', this.props.label ?? '', error)
  }
  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div className="guard">
        <span>Something went wrong here.</span>
        <button className="btn secondary" onClick={() => this.setState({ failed: false })}>Try again</button>
        <button className="btn text" onClick={() => window.location.reload()}>Reload page</button>
      </div>
    )
  }
}
