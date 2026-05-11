import {
  type User,
  onAuthStateChanged,
  signInWithPopup,
  signOut,
} from 'firebase/auth';
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { auth, googleProvider } from '../firebase';

type AuthContextValue = {
  currentUser: User | null;
  loading: boolean;
  error: string | null;
  loginWithGoogle: () => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
}

type AuthProviderProps = {
  children: ReactNode;
};

export function AuthProvider({ children }: AuthProviderProps) {
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    return onAuthStateChanged(auth, (user) => {
      setCurrentUser(user);
      setLoading(false);
    });
  }, []);

  const loginWithGoogle = async () => {
    setError(null);
    try {
      await signInWithPopup(auth, googleProvider);
    } catch (err) {
      if (err instanceof Error) {
        setError(err.message);
        return;
      }
      setError('Googleログインに失敗しました');
    }
  };

  const logout = async () => {
    setError(null);
    await signOut(auth);
  };

  const value = useMemo(
    () => ({
      currentUser,
      loading,
      error,
      loginWithGoogle,
      logout,
    }),
    [currentUser, loading, error],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function getReadableAuthError(message: string) {
  if (message.includes('auth/unauthorized-domain')) {
    return 'このURLはFirebase Authenticationの承認済みドメインに登録されていません。';
  }
  if (message.includes('auth/popup-closed-by-user')) {
    return 'ログイン画面が閉じられました。';
  }
  if (message.includes('auth/cancelled-popup-request')) {
    return 'ログイン処理がキャンセルされました。';
  }
  return 'Googleログインに失敗しました。';
}
