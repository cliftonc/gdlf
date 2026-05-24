import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import {
  Button,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
} from "@heroui/react";

type ConfirmOpts = {
  title: string;
  body?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
};

type Ctx = (opts: ConfirmOpts) => Promise<boolean>;

const ConfirmContext = createContext<Ctx | null>(null);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [opts, setOpts] = useState<ConfirmOpts | null>(null);
  const resolver = useRef<((b: boolean) => void) | null>(null);

  const confirm = useCallback<Ctx>((next) => {
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
      setOpts(next);
    });
  }, []);

  const close = (result: boolean) => {
    resolver.current?.(result);
    resolver.current = null;
    setOpts(null);
  };

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Modal
        isOpen={!!opts}
        onOpenChange={(open) => {
          if (!open) close(false);
        }}
        placement="center"
        backdrop="blur"
        size="sm"
      >
        <ModalContent>
          {opts && (
            <>
              <ModalHeader>{opts.title}</ModalHeader>
              {opts.body && <ModalBody>{opts.body}</ModalBody>}
              <ModalFooter>
                <Button variant="light" onPress={() => close(false)}>
                  {opts.cancelLabel ?? "Cancel"}
                </Button>
                <Button
                  color={opts.danger ? "danger" : "primary"}
                  onPress={() => close(true)}
                >
                  {opts.confirmLabel ?? "Confirm"}
                </Button>
              </ModalFooter>
            </>
          )}
        </ModalContent>
      </Modal>
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): Ctx {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error("useConfirm must be used inside <ConfirmProvider>");
  return ctx;
}
