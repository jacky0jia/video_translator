import { Component } from 'react';

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    this.setState({ errorInfo });
    console.error('ErrorBoundary caught:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: '20px', fontFamily: 'monospace', fontSize: '14px', color: '#b91c1c', background: '#fef2f2', minHeight: '100vh' }}>
          <h2 style={{ marginBottom: '16px', color: '#b91c1c' }}>⚠️ React 渲染错误</h2>
          <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all', background: '#fff', padding: '12px', borderRadius: '8px', border: '1px solid #fecaca' }}>
            {this.state.error?.toString()}
          </pre>
          {this.state.errorInfo?.componentStack && (
            <details style={{ marginTop: '16px' }}>
              <summary style={{ cursor: 'pointer', color: '#b91c1c' }}>组件堆栈</summary>
              <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all', background: '#fff', padding: '12px', borderRadius: '8px', border: '1px solid #fecaca', marginTop: '8px' }}>
                {this.state.errorInfo.componentStack}
              </pre>
            </details>
          )}
          <button
            onClick={() => { this.setState({ hasError: false, error: null, errorInfo: null }); window.location.reload(); }}
            style={{ marginTop: '16px', padding: '8px 16px', background: '#3b82f6', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer' }}
          >
            重新加载
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
