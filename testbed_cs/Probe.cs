using Godot;

// Counts physics ticks, and answers eval with a C# method.
public partial class Probe : Node3D
{
    int ticks;

    public override void _PhysicsProcess(double delta) => ticks++;

    public int Ticks() => ticks;

    public int Answer() => 42;
}
