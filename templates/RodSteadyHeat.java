import com.comsol.model.*;
import com.comsol.model.util.*;
import java.util.Locale;

/** Steady 1D conduction, uniform k, insulated sides and no volumetric source. */
public class RodSteadyHeat {
  public static Model run() {
    Model model = ModelUtil.create("Model");
    model.label("rod_steady_heat.mph");
    model.param().set("L", "@@LENGTH@@[mm]", "Rod length");
    model.param().set("k", "@@K@@[W/(m*K)]", "Uniform thermal conductivity");
    model.param().set("T_left", "@@LEFT_K@@[K]", "Left temperature: @@LEFT_C@@ degC");
    model.param().set("T_right", "@@RIGHT_K@@[K]", "Right temperature: @@RIGHT_C@@ degC");
    model.param().set("Q_vol", "0[W/m^3]", "No internal heat generation");
    model.param().set("N_elements", "@@ELEMENTS@@", "Uniform 1D mesh element count");

    model.component().create("comp1", true);
    model.component("comp1").label("Uniform rod - 1D axial conduction");
    model.component("comp1").geom().create("geom1", 1);
    model.component("comp1").geom("geom1").lengthUnit("m");
    model.component("comp1").geom("geom1").create("rod", "Interval");
    model.component("comp1").geom("geom1").feature("rod").set("coord", new String[]{"0", "L"});
    model.component("comp1").geom("geom1").run();

    model.component("comp1").physics().create("c", "CoefficientFormPDE", "geom1");
    model.component("comp1").physics("c").label("Heat conduction: -d/dx(k*dT/dx) = Q_vol");
    model.component("comp1").physics("c").field("dimensionless").field("T");
    model.component("comp1").physics("c").field("dimensionless").component(new String[]{"T"});
    model.component("comp1").physics("c").prop("Units").set("DependentVariableQuantity", "temperature");
    model.component("comp1").physics("c").prop("Units").setIndex("CustomSourceTermUnit", "W/m^3", 0, 0);
    model.component("comp1").physics("c").feature("cfeq1").set("c", "k");
    model.component("comp1").physics("c").feature("cfeq1").set("a", "0");
    model.component("comp1").physics("c").feature("cfeq1").set("f", "Q_vol");
    model.component("comp1").physics("c").feature("cfeq1").set("da", "0");
    model.component("comp1").physics("c").feature("cfeq1").set("ea", "0");
    model.component("comp1").physics("c").feature("init1").set("T", "(T_left+T_right)/2");
    model.component("comp1").physics("c").create("left", "DirichletBoundary", 0);
    model.component("comp1").physics("c").feature("left").label("Left endpoint - @@LEFT_C@@ degC");
    model.component("comp1").physics("c").feature("left").selection().set(new int[]{1});
    model.component("comp1").physics("c").feature("left").set("r", "T_left");
    model.component("comp1").physics("c").create("right", "DirichletBoundary", 0);
    model.component("comp1").physics("c").feature("right").label("Right endpoint - @@RIGHT_C@@ degC");
    model.component("comp1").physics("c").feature("right").selection().set(new int[]{2});
    model.component("comp1").physics("c").feature("right").set("r", "T_right");

    model.component("comp1").variable().create("var1");
    model.component("comp1").variable("var1").set("T_exact", "T_left+(T_right-T_left)*x/L", "Analytical temperature");
    model.component("comp1").variable("var1").set("qx", "-k*Tx", "Axial heat flux, positive in +x");
    model.component("comp1").variable("var1").set("abs_T_error", "abs(T-T_exact)", "Absolute temperature error");
    model.component("comp1").mesh().create("mesh1");
    model.component("comp1").mesh("mesh1").create("edge1", "Edge");
    model.component("comp1").mesh("mesh1").feature("edge1").create("dist1", "Distribution");
    model.component("comp1").mesh("mesh1").feature("edge1").feature("dist1").set("numelem", "N_elements");
    model.component("comp1").mesh("mesh1").feature("edge1").feature("dist1").set("equidistant", "on");
    model.component("comp1").mesh("mesh1").run();
    model.study().create("std1");
    model.study("std1").label("Steady state");
    model.study("std1").create("stat", "Stationary");
    model.study("std1").run();

    model.result().create("pg1", "PlotGroup1D");
    model.result("pg1").label("Temperature along rod (degC versus mm)");
    model.result("pg1").set("titletype", "manual");
    model.result("pg1").set("title", "Steady 1D rod temperature: L=@@LENGTH@@ mm, k=@@K@@ W/(m K)");
    model.result("pg1").set("xlabelactive", "on");
    model.result("pg1").set("xlabel", "Position x (mm)");
    model.result("pg1").set("ylabelactive", "on");
    model.result("pg1").set("ylabel", "Temperature (degC)");
    String[] expressions = new String[]{"T", "T_exact"};
    String[] legends = new String[]{"COMSOL finite element solution", "Analytical solution"};
    for (int i=0; i<2; i++) {
      String tag = "line"+(i+1);
      model.result("pg1").create(tag, "LineGraph");
      model.result("pg1").feature(tag).selection().all();
      model.result("pg1").feature(tag).set("expr", expressions[i]);
      model.result("pg1").feature(tag).set("unit", "degC");
      model.result("pg1").feature(tag).set("xdata", "expr");
      model.result("pg1").feature(tag).set("xdataexpr", "x/1[mm]");
      model.result("pg1").feature(tag).set("legend", "on");
      model.result("pg1").feature(tag).set("legendmethod", "manual");
      model.result("pg1").feature(tag).set("legends", new String[]{legends[i]});
      if (i==1) model.result("pg1").feature(tag).set("linestyle", "dashed");
    }

    double[] coordinates = new double[@@SAMPLES@@];
    for (int i=0; i<@@SAMPLES@@; i++) coordinates[i] = i * (@@LENGTH@@ * 0.001) / (@@SAMPLES@@ - 1);
    model.result().numerical().create("sample21", "Interp");
    model.result().numerical("sample21").set("expr", new String[]{"T", "qx"});
    model.result().numerical("sample21").setInterpolationCoordinates(new double[][]{coordinates});
    double[][][] sampled = model.result().numerical("sample21").getData();
    double[][] rows = new double[@@SAMPLES@@][6];
    double maxError = 0;
    double maxFluxError = 0;
    for (int i=0; i<@@SAMPLES@@; i++) {
      double celsius = sampled[0][0][i]-273.15;
      double exact = @@LEFT_C@@ + (@@RIGHT_C@@ - @@LEFT_C@@) * coordinates[i] / (@@LENGTH@@ * 0.001);
      double flux = sampled[1][0][i];
      rows[i] = new double[]{coordinates[i]*1000, celsius, sampled[0][0][i], exact, Math.abs(celsius-exact), flux};
      maxError = Math.max(maxError, Math.abs(celsius-exact));
      maxFluxError = Math.max(maxFluxError, Math.abs(flux-(@@K@@ * (@@LEFT_C@@ - @@RIGHT_C@@) / (@@LENGTH@@ * 0.001))));
      if (!Double.isFinite(celsius) || !Double.isFinite(flux)) throw new IllegalStateException("Nonfinite solution");
    }
    double expectedFlux = @@K@@ * (@@LEFT_C@@ - @@RIGHT_C@@) / (@@LENGTH@@ * 0.001);
    if (maxError>1e-6 || maxFluxError>(0.1+1e-8*Math.abs(expectedFlux))) {
      throw new IllegalStateException("Verification failed: max T error="+maxError+", max flux error="+maxFluxError);
    }
    model.result().table().create("tbl21", "Table");
    model.result().table("tbl21").label("Equidistant temperature samples with endpoints");
    model.result().table("tbl21").setColumnHeaders(new String[]{"position_mm", "temperature_C", "temperature_K", "analytical_temperature_C", "absolute_error_K", "heat_flux_W_m2"});
    model.result().table("tbl21").setTableData(rows);
    model.result().numerical().create("maxerr", "MaxLine");
    model.result().numerical("maxerr").label("Maximum absolute temperature error (K)");
    model.result().numerical("maxerr").selection().all();
    model.result().numerical("maxerr").set("expr", "abs_T_error");
    double domainError = model.result().numerical("maxerr").getReal()[0][0];
    if (!Double.isFinite(domainError) || domainError>1e-6) throw new IllegalStateException("Domain error check failed: "+domainError);
    model.param().set("verification_max_temperature_error", Double.toString(domainError)+"[K]", "Maximum error against exact linear profile");
    model.param().set("verification_midpoint_temperature", Double.toString(rows[@@MID_INDEX@@][2])+"[K]", "Computed midpoint temperature");

    System.out.println("ROD_DATA_BEGIN");
    System.out.println("position_mm,temperature_C,temperature_K,analytical_temperature_C,absolute_error_K,heat_flux_W_m2");
    for (double[] row : rows) {
      System.out.printf(Locale.US, "%.15g,%.15g,%.15g,%.15g,%.15g,%.15g%n", row[0],row[1],row[2],row[3],row[4],row[5]);
    }
    System.out.println("ROD_DATA_END");
    System.out.println("ROD_CHECK_PASS midpoint_C="+rows[@@MID_INDEX@@][1]+" max_temperature_error_K="+domainError+" max_heat_flux_error_W_m2="+maxFluxError);
    return model;
  }
  public static void main(String[] args) { run(); }
}
