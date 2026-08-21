import { useHealth } from "@/hooks/useHealth";

function App() {
  const { data, isLoading, isError } = useHealth();

  return (
    <main className="flex min-h-screen items-center justify-center bg-background text-foreground">
      <div className="text-center">
        <h1 className="text-2xl font-semibold">AI Document Router</h1>
        {isLoading && <p className="mt-2 text-muted-foreground">Checking backend…</p>}
        {isError && <p className="mt-2 text-destructive">Backend unreachable</p>}
        {data && <p className="mt-2 text-muted-foreground">API status: {data.status}</p>}
      </div>
    </main>
  );
}

export default App;
