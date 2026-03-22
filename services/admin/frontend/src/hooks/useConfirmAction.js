import { useCallback, useRef, useState } from 'react';

export function useConfirmAction() {
  const resolverRef = useRef(null);
  const [dialog, setDialog] = useState({
    open: false,
    title: '',
    description: '',
    confirmLabel: 'Confirm',
    destructive: false,
  });

  const confirm = useCallback(
    ({ title, description, confirmLabel = 'Confirm', destructive = false }) =>
      new Promise((resolve) => {
        resolverRef.current = resolve;
        setDialog({ open: true, title, description, confirmLabel, destructive });
      }),
    []
  );

  const close = useCallback((confirmed) => {
    const resolve = resolverRef.current;
    resolverRef.current = null;
    setDialog({
      open: false,
      title: '',
      description: '',
      confirmLabel: 'Confirm',
      destructive: false,
    });
    resolve?.(confirmed);
  }, []);

  return { dialog, confirm, close };
}
