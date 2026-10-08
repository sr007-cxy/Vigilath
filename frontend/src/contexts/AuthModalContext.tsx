import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { AuthModal } from '../components/AuthModal';

interface AuthModalOptions {
  /** What to do after a successful login. Not passed = stay in place (an overlay shouldn't kick people away). */
  onSuccess?: () => void;
}

interface AuthModalContextValue {
  openAuthModal: (tab?: 'login' | 'register' | 'phone' | 'wechat', opts?: AuthModalOptions) => void;
  closeAuthModal: () => void;
}

const AuthModalContext = createContext<AuthModalContextValue | undefined>(undefined);

export function AuthModalProvider({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const [tab, setTab] = useState<'login' | 'register' | 'phone' | 'wechat'>('login');
  // Stored in a ref rather than state: changing the callback shouldn't re-render the Provider (it wraps the whole tree).
  const onSuccessRef = useRef<(() => void) | undefined>(undefined);

  const openAuthModal = useCallback(
    (defaultTab: 'login' | 'register' | 'phone' | 'wechat' = 'login', opts?: AuthModalOptions) => {
      onSuccessRef.current = opts?.onSuccess;
      setTab(defaultTab);
      setIsOpen(true);
    },
    [],
  );

  const closeAuthModal = useCallback(() => {
    setIsOpen(false);
    onSuccessRef.current = undefined;   // don't let the previous callback leak into the next one
  }, []);

  return (
    <AuthModalContext.Provider value={{ openAuthModal, closeAuthModal }}>
      {children}
      <AuthModal
        isOpen={isOpen}
        onClose={closeAuthModal}
        defaultTab={tab}
        onSuccess={onSuccessRef.current}
      />
    </AuthModalContext.Provider>
  );
}

export function useAuthModal(): AuthModalContextValue {
  const ctx = useContext(AuthModalContext);
  if (!ctx) {
    throw new Error('useAuthModal must be used within <AuthModalProvider>.');
  }
  return ctx;
}
