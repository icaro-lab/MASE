import React, { useEffect, useRef, useState } from 'react';
import { cn } from 'lib/utils';
import { buttonVariants } from './button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from './alert-dialog';

export const MODAL_CONFIRM_EVENT = 'mase:modal-confirm';

export function ConfirmDialogHost() {
  const [open, setOpen] = useState(false);
  const [spec, setSpec] = useState(null);
  const resolverRef = useRef(null);

  useEffect(() => {
    const handler = (event) => {
      const detail = event.detail || {};
      setSpec(detail);
      setOpen(true);
    };
    window.addEventListener(MODAL_CONFIRM_EVENT, handler);
    return () => window.removeEventListener(MODAL_CONFIRM_EVENT, handler);
  }, []);

  const resolve = (accepted) => {
    const onOk = spec?.onOk;
    const onCancel = spec?.onCancel;
    if (accepted) onOk?.();
    else onCancel?.();
    resolverRef.current?.(accepted);
    resolverRef.current = null;
    setOpen(false);
    setSpec(null);
  };

  return (
    <AlertDialog open={open} onOpenChange={(nextOpen) => (!nextOpen ? resolve(false) : setOpen(nextOpen))}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{spec?.title || 'Confirm action'}</AlertDialogTitle>
          <AlertDialogDescription>{spec?.content || ''}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{spec?.cancelText || 'Cancel'}</AlertDialogCancel>
          <AlertDialogAction
            className={cn(spec?.okType === 'danger' ? buttonVariants({ variant: 'destructive' }) : null)}
            onClick={() => resolve(true)}
          >
            {spec?.okText || 'Confirm'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export const Modal = {
  confirm(options = {}) {
    if (typeof window === 'undefined') return;
    const event = new CustomEvent(MODAL_CONFIRM_EVENT, {
      detail: options,
    });
    window.dispatchEvent(event);
  },
};
